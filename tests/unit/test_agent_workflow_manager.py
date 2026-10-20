# START OF FILE tests/unit/test_agent_workflow_manager.py
"""
Tests for AgentWorkflowManager.get_system_prompt(): governance principles and prompt assembly.

Round 2m: the old tests could not run - they used AGENT_TYPE_* without importing them, put the prompt templates on
manager.settings (get_system_prompt reads the module-level src.config.settings.settings) and mapped states to tuples
instead of prompt keys. These tests patch the module-level settings and use the real state -> prompt key map.

Rules under test:
* Enabled principles whose 'applies_to' contains 'all_agents' or the agent's type are appended to the agent's standard
  framework instructions as "Principle: <name> (ID: <id>)\\n<text>" between "--- Governance Principles ---" and
  "--- End Governance Principles ---"; disabled or non-matching principles are left out; no section if none apply.
* The standard instructions (with the principles) are placed where the state prompt has
  {<type>_standard_framework_instructions}; the personality text where it has {personality_instructions}.
* With the real prompts.yaml, every mapped (agent type, state) prompt formats without falling back.
"""
import logging
import types
import unittest
from unittest.mock import MagicMock, patch

from src.agents import workflow_manager
from src.agents.constants import (
    ADMIN_STATE_CONVERSATION, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER, PM_STATE_MANAGE, WORKER_STATE_WORK,
)
from src.agents.workflow_manager import AgentWorkflowManager
from src.config.settings import settings as real_settings

STATE_FOR_TYPE = {AGENT_TYPE_ADMIN: ADMIN_STATE_CONVERSATION, AGENT_TYPE_PM: PM_STATE_MANAGE,
                  AGENT_TYPE_WORKER: WORKER_STATE_WORK}

TEST_PROMPTS = {
    "admin_standard_framework_instructions": "Admin Standard Instructions {agent_id} {team_id} {project_name}",
    "pm_standard_framework_instructions": "PM Standard Instructions {agent_id} {team_id} {project_name}",
    "worker_standard_framework_instructions": "Worker Standard Instructions {agent_id} {team_id} {project_name}",
    "default_system_prompt": "Default System Prompt.",
    "admin_ai_conversation_prompt": "Admin Test State Prompt. {admin_standard_framework_instructions} {personality_instructions}",
    "pm_manage_prompt": "PM Test State Prompt. {pm_standard_framework_instructions}",
    "worker_work_prompt": "Worker Test State Prompt. {worker_standard_framework_instructions} Task: {task_description}",
}

PRIVACY = {"id": "GP001", "name": "Privacy", "text": "User privacy is key.", "applies_to": ["all_agents"], "enabled": True}


def make_agent(agent_type, state=None):
    return types.SimpleNamespace(
        agent_id="test_agent_gp", agent_type=agent_type, persona="Test Persona GP",
        state=state or STATE_FOR_TYPE[agent_type], _config_system_prompt="Admin personality instructions.",
        agent_config={"config": {}}, message_history=[], initial_plan_description="Build the thing.",
        current_task_description="Build the thing.", provider_name="ollama-local-test", model="qwen3.5:9b",
        role="Developer")


def make_manager(agent):
    state_manager = MagicMock()
    state_manager.get_agent_team.return_value = "test_team_gp"
    state_manager.get_agents_in_team.return_value = [agent]
    return types.SimpleNamespace(state_manager=state_manager, current_project="TestProjectGP",
                                 current_session="TestSessionGP", agents={agent.agent_id: agent}, bootstrap_agents=[])


class TestGovernancePrinciplesInPrompt(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.settings = types.SimpleNamespace(PROMPTS=dict(TEST_PROMPTS), GOVERNANCE_PRINCIPLES=[],
                                              NATIVE_TOOL_CALLING_ENABLED=True)
        patcher = patch.object(workflow_manager, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.awm = AgentWorkflowManager()

    def prompt_for(self, agent_type, principles):
        self.settings.GOVERNANCE_PRINCIPLES = principles
        agent = make_agent(agent_type)
        prompt = self.awm.get_system_prompt(agent, make_manager(agent))
        self.assertEqual(agent.final_system_prompt, prompt)
        return prompt

    def test_multiple_applicable_principles(self):
        prompt = self.prompt_for(AGENT_TYPE_PM, [
            PRIVACY,
            {"id": "GP002", "name": "PM Specific", "text": "PMs must manage tasks.", "applies_to": ["pm"], "enabled": True},
            {"id": "GP003", "name": "Worker Only", "text": "Workers do work.", "applies_to": ["worker"], "enabled": True},
            {"id": "GP004", "name": "Disabled PM", "text": "Old PM rule.", "applies_to": ["pm"], "enabled": False},
        ])
        self.assertTrue(prompt.startswith("PM Test State Prompt. PM Standard Instructions test_agent_gp test_team_gp"))
        self.assertIn("\n\n--- Governance Principles ---\n"
                      "Principle: Privacy (ID: GP001)\nUser privacy is key.\n"
                      "Principle: PM Specific (ID: GP002)\nPMs must manage tasks.\n"
                      "--- End Governance Principles ---\n", prompt)
        self.assertNotIn("Worker Only", prompt)
        self.assertNotIn("Disabled PM", prompt)

    def test_no_applicable_principles_for_agent_type(self):
        prompt = self.prompt_for(AGENT_TYPE_WORKER, [
            {"id": "GP001", "name": "Admin Only", "text": "Admin rule.", "applies_to": ["admin"], "enabled": True},
            {"id": "GP002", "name": "PM Only", "text": "PM rule.", "applies_to": ["pm"], "enabled": True},
        ])
        self.assertTrue(prompt.startswith("Worker Test State Prompt. Worker Standard Instructions"))
        self.assertNotIn("Governance Principles", prompt)

    def test_principle_applicable_via_all_agents(self):
        prompt = self.prompt_for(AGENT_TYPE_WORKER, [
            {"id": "GP001", "name": "Universal Rule", "text": "Applies to everyone.", "applies_to": ["all_agents"],
             "enabled": True}])
        self.assertIn("--- Governance Principles ---\nPrinciple: Universal Rule (ID: GP001)\nApplies to everyone.\n", prompt)
        # the principles stay inside the standard instructions, before the rest of the state prompt
        self.assertLess(prompt.find("--- End Governance Principles ---"), prompt.find("Task: Build the thing."))

    def test_missing_enabled_flag_or_id(self):
        prompt = self.prompt_for(AGENT_TYPE_WORKER, [
            {"id": "GP001", "name": "No Flag", "text": "Not enabled.", "applies_to": ["all_agents"]},
            {"name": "No Id", "text": "Enabled without id.", "applies_to": ["worker"], "enabled": True},
        ])
        self.assertNotIn("No Flag", prompt)
        self.assertIn("Principle: No Id (ID: N/A)\nEnabled without id.", prompt)

    def test_no_enabled_principles(self):
        prompt = self.prompt_for(AGENT_TYPE_PM, [
            dict(PRIVACY, enabled=False),
            {"id": "GP002", "name": "PM Specific", "text": "PMs must manage tasks.", "applies_to": ["pm"], "enabled": False},
        ])
        self.assertNotIn("Governance Principles", prompt)

    def test_empty_governance_principles_list(self):
        self.assertNotIn("Governance Principles", self.prompt_for(AGENT_TYPE_ADMIN, []))

    def test_injection_order(self):
        prompt = self.prompt_for(AGENT_TYPE_ADMIN, [PRIVACY])
        positions = [prompt.find(text) for text in ("Admin Test State Prompt.", "Admin Standard Instructions",
                                                    "--- Governance Principles ---", "--- End Governance Principles ---",
                                                    "Admin personality instructions.")]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_unknown_state_uses_the_default_prompt(self):
        agent = make_agent(AGENT_TYPE_WORKER, state="no_such_state")
        self.assertEqual(self.awm.get_system_prompt(agent, make_manager(agent)), "Default System Prompt.")


class TestRealPromptTemplates(unittest.TestCase):
    """Every mapped (agent type, state) prompt of the real prompts.yaml formats without the fallback."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.awm = AgentWorkflowManager()

    def test_all_mapped_prompts_format(self):
        self.assertTrue(real_settings.PROMPTS, "prompts.yaml was not loaded")
        for (agent_type, state), prompt_key in self.awm._prompt_map.items():
            with self.subTest(agent_type=agent_type, state=state):
                self.assertIn(prompt_key, real_settings.PROMPTS)
                agent = make_agent(agent_type, state=state)
                prompt = self.awm.get_system_prompt(agent, make_manager(agent))
                self.assertNotIn("Full state-specific prompt formatting failed", prompt)
                self.assertNotIn("Error: State-specific prompt template missing.", prompt)
                self.assertNotIn("Error: Standard instructions template missing.", prompt)


if __name__ == '__main__':
    unittest.main()
