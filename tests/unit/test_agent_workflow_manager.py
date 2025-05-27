import unittest
from unittest.mock import MagicMock, patch

# Import the class to be tested
from src.agents.workflow_manager import AgentWorkflowManager
# Import classes to be mocked
from src.agents.core import Agent
from src.agents.manager import AgentManager
from src.config.settings import Settings # To mock settings.GOVERNANCE_PRINCIPLES

# Disable most logging output for tests unless specifically testing logging
import logging
logging.disable(logging.CRITICAL)

class TestAgentWorkflowManagerGovernancePrompt(unittest.TestCase):

    def setUp(self):
        self.awm = AgentWorkflowManager() # Instantiate the class we are testing

        # Mock Agent
        self.mock_agent = MagicMock(spec=Agent)
        self.mock_agent.agent_id = "test_agent_gp"
        # agent_type will be set per test
        self.mock_agent.persona = "Test Persona GP"
        self.mock_agent.state = "test_state" # A generic state for prompt lookup
        # Mock _config_system_prompt for Admin AI personality instructions
        self.mock_agent._config_system_prompt = "Admin personality instructions."


        # Mock AgentManager
        self.mock_manager = MagicMock(spec=AgentManager)
        
        # Mock manager.settings and its GOVERNANCE_PRINCIPLES attribute
        self.mock_manager.settings = MagicMock(spec=Settings)
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [] # Default to empty
        
        # AgentWorkflowManager uses manager.settings.PROMPTS for prompt templates
        # Provide minimal prompt templates needed by get_system_prompt
        self.mock_manager.settings.PROMPTS = {
            "admin_standard_framework_instructions": "Admin Standard Instructions {agent_id} {team_id} {project_name} {session_name} {current_time_utc} {address_book} {available_workflow_trigger}",
            "pm_standard_framework_instructions": "PM Standard Instructions {agent_id} {team_id} {project_name} {session_name} {current_time_utc} {address_book} {available_workflow_trigger}",
            "worker_standard_framework_instructions": "Worker Standard Instructions {agent_id} {team_id} {project_name} {session_name} {current_time_utc} {address_book} {available_workflow_trigger}",
            "default_system_prompt": "Default System Prompt.",
            # Add state-specific prompt keys that might be looked up by _prompt_map
            ("admin", "test_state"): "Admin Test State Prompt. {admin_standard_framework_instructions} {personality_instructions}",
            ("pm", "test_state"): "PM Test State Prompt. {pm_standard_framework_instructions}",
            ("worker", "test_state"): "Worker Test State Prompt. {worker_standard_framework_instructions}",
        }
        # Add entries to _prompt_map in awm for the test_state
        self.awm._prompt_map[(AGENT_TYPE_ADMIN, "test_state")] = ("admin", "test_state")
        self.awm._prompt_map[(AGENT_TYPE_PM, "test_state")] = ("pm", "test_state")
        self.awm._prompt_map[(AGENT_TYPE_WORKER, "test_state")] = ("worker", "test_state")


        # Mock other manager components that get_system_prompt might interact with indirectly
        self.mock_manager.state_manager = MagicMock()
        self.mock_manager.state_manager.get_agent_team.return_value = "test_team_gp"
        self.mock_manager.current_project = "TestProjectGP"
        self.mock_manager.current_session = "TestSessionGP"
        self.mock_manager.agents = {"test_agent_gp": self.mock_agent} # Manager needs to know about the agent

    def test_scenario_1_multiple_applicable_principles(self):
        self.mock_agent.agent_type = "pm"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [
            {"id": "GP001", "name": "Privacy", "text": "User privacy is key.", "applies_to": ["all_agents"], "enabled": True},
            {"id": "GP002", "name": "PM Specific", "text": "PMs must manage tasks.", "applies_to": ["pm"], "enabled": True},
            {"id": "GP003", "name": "Worker Only", "text": "Workers do work.", "applies_to": ["worker"], "enabled": True},
            {"id": "GP004", "name": "Disabled PM", "text": "Old PM rule.", "applies_to": ["pm"], "enabled": False},
        ]

        prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)

        self.assertIn("--- Governance Principles ---", prompt)
        self.assertIn("Principle: Privacy (ID: GP001)\nUser privacy is key.", prompt)
        self.assertIn("Principle: PM Specific (ID: GP002)\nPMs must manage tasks.", prompt)
        self.assertNotIn("Principle: Worker Only", prompt)
        self.assertNotIn("Principle: Disabled PM", prompt)
        self.assertIn("--- End Governance Principles ---", prompt)
        self.assertTrue(prompt.startswith("PM Test State Prompt.")) # Check base prompt
        self.assertIn("PM Standard Instructions", prompt) # Check standard instructions were included

    def test_scenario_2_no_applicable_principles_for_agent_type(self):
        self.mock_agent.agent_type = "worker"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [
            {"id": "GP001", "name": "Admin Only", "text": "Admin rule.", "applies_to": ["admin"], "enabled": True},
            {"id": "GP002", "name": "PM Only", "text": "PM rule.", "applies_to": ["pm"], "enabled": True},
        ]
        prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)
        self.assertNotIn("--- Governance Principles ---", prompt)

    def test_scenario_3_principle_applicable_via_all_agents(self):
        self.mock_agent.agent_type = "worker"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [
            {"id": "GP001", "name": "Universal Rule", "text": "Applies to everyone.", "applies_to": ["all_agents"], "enabled": True},
        ]
        prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)
        self.assertIn("--- Governance Principles ---", prompt)
        self.assertIn("Principle: Universal Rule (ID: GP001)\nApplies to everyone.", prompt)

    def test_scenario_4_no_enabled_principles(self):
        self.mock_agent.agent_type = "pm"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [
            {"id": "GP001", "name": "Privacy - Disabled", "text": "User privacy is key.", "applies_to": ["all_agents"], "enabled": False},
            {"id": "GP002", "name": "PM Specific - Disabled", "text": "PMs must manage tasks.", "applies_to": ["pm"], "enabled": False},
        ]
        prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)
        self.assertNotIn("--- Governance Principles ---", prompt)

    def test_scenario_5_empty_governance_principles_list(self):
        self.mock_agent.agent_type = "admin"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [] # Empty list
        prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)
        self.assertNotIn("--- Governance Principles ---", prompt)

    def test_governance_principles_injection_order(self):
        self.mock_agent.agent_type = "admin"
        self.mock_manager.settings.GOVERNANCE_PRINCIPLES = [
            {"id": "GP001", "name": "Universal Rule", "text": "Applies to everyone.", "applies_to": ["all_agents"], "enabled": True},
        ]
        
        # Mock the _build_address_book to simplify checking order against standard instructions
        with patch.object(self.awm, '_build_address_book', return_value="Mocked Address Book") as mock_addr_book:
            prompt = self.awm.get_system_prompt(self.mock_agent, self.mock_manager)

            # Expected order roughly: State Prompt Start -> Standard Instructions -> Governance Principles -> Personality
            # The state prompt template itself includes {standard_framework_instructions} and {personality_instructions}
            # The governance principles are appended to formatted_standard_instructions.
            
            # Find standard instructions part
            std_instr_start_index = prompt.find("Admin Standard Instructions")
            self.assertGreater(std_instr_start_index, -1, "Standard instructions not found in prompt")
            
            # Find governance principles part
            gov_princ_start_index = prompt.find("--- Governance Principles ---")
            self.assertGreater(gov_princ_start_index, -1, "Governance principles section not found")
            
            # Find personality instructions part (if applicable for admin)
            # The template for admin includes {personality_instructions} *after* {admin_standard_framework_instructions}
            # And governance is appended to admin_standard_framework_instructions.
            # So, std_instr -> governance -> then the rest of the state prompt which might include personality.
            # The key is that governance is *part of* the formatted standard instructions block.

            # Check that governance comes after the start of standard instructions but before the end of the standard block logic
            # The standard instructions template ends with "... {available_workflow_trigger}"
            # The governance block is appended directly after this formatted standard instruction.
            
            end_of_std_instr_placeholder = self.mock_manager.settings.PROMPTS["admin_standard_framework_instructions"].split("{available_workflow_trigger}")[0]
            # We need to find where the actual content of standard instructions ends, before governance is appended.
            # The standard instructions are formatted, then governance is appended to that.
            # Then this combined block is used to format the state-specific prompt.

            self.assertIn("Admin Standard Instructions", prompt)
            self.assertIn("--- Governance Principles ---", prompt)
            self.assertIn("Admin personality instructions.", prompt) # From agent._config_system_prompt

            # More precise check:
            # 1. Standard instructions are formatted.
            # 2. Governance string is appended to this.
            # 3. This combined string replaces {admin_standard_framework_instructions} in the state prompt.
            # 4. {personality_instructions} is also replaced in the state prompt.

            # The state prompt for admin is: "Admin Test State Prompt. {admin_standard_framework_instructions} {personality_instructions}"
            # So, the order should be:
            # "Admin Test State Prompt. " 
            # + "Admin Standard Instructions ... {available_workflow_trigger_value}" 
            # + "--- Governance Principles --- ..."
            # + "Admin personality instructions."

            idx_state_start = prompt.find("Admin Test State Prompt.")
            idx_std_instr_start = prompt.find("Admin Standard Instructions")
            idx_gov_start = prompt.find("--- Governance Principles ---")
            idx_personality_start = prompt.find("Admin personality instructions.")

            self.assertTrue(idx_state_start < idx_std_instr_start)
            self.assertTrue(idx_std_instr_start < idx_gov_start)
            # The personality instructions are part of the state_prompt_template,
            # and standard_instructions (which includes governance) is also formatted into it.
            # So the relative order depends on the state_prompt_template structure.
            # For "admin" and "test_state": "Admin Test State Prompt. {admin_standard_framework_instructions} {personality_instructions}"
            # This means std_instr (now with gov appended) comes before personality.
            self.assertTrue(idx_gov_start < idx_personality_start)


if __name__ == '__main__':
    unittest.main()
