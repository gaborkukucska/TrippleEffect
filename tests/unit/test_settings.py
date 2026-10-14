import unittest
from unittest.mock import patch, mock_open, MagicMock
import yaml # For YAMLError simulation
import logging

# We need to import Settings from the module where it's defined.
# Since Settings instantiates ModelRegistry and calls its own methods upon __init__,
# we need to be careful about what we patch *before* importing Settings or instantiating it.

# To prevent `config_manager` and `ModelRegistry` from running their actual __init__ logic
# during the import of `src.config.settings` or when Settings is instantiated,
# we patch them at the module level where they are defined or imported.

# Patch config_manager where it's imported by settings.py
patch_config_manager = patch('src.config.settings.config_manager', MagicMock())
# Patch ModelRegistryClass where it's imported by settings.py
patch_model_registry_class = patch('src.config.settings.ModelRegistryClass', MagicMock())

# Start the patches before importing Settings
patch_config_manager.start()
patch_model_registry_class.start()

from src.config.settings import Settings, BASE_DIR, GOVERNANCE_FILE_PATH

# Disable logging for most tests, enable specifically if testing log messages
logging.disable(logging.CRITICAL)


class TestSettingsGovernanceLoading(unittest.TestCase):

    def tearDown(self):
        # Ensure patches are stopped after each test if they were started in setUp or test methods
        # However, for module-level patches like these, it's often better to manage them outside
        # if they are meant to affect all tests in the class/module.
        # For class-level or method-level, setUp/tearDown or with statement is better.
        pass

    @classmethod
    def tearDownClass(cls):
        # Stop module-level patches after all tests in the class have run
        patch_config_manager.stop()
        patch_model_registry_class.stop()

    def get_settings_instance(self):
        # Helper to get a Settings instance, ensuring patches are active.
        # This re-imports or re-uses the already patched environment for Settings.
        # For these tests, Settings() is called, which triggers _load_governance_principles.
        return Settings()

    @patch('src.config.settings.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    def test_scenario_1_valid_governance_yaml(self, mock_yaml_load, mock_file_open, mock_path_exists):
        mock_path_exists.return_value = True # governance.yaml exists
        valid_principles_data = {
            "principles": [
                {"id": "GP001", "name": "Test Principle 1", "text": "Text 1", "applies_to": ["all_agents"], "enabled": True},
                {"id": "GP002", "name": "Test Principle 2", "text": "Text 2", "applies_to": ["worker"], "enabled": False},
            ]
        }
        mock_yaml_load.return_value = valid_principles_data
        
        # Instantiate Settings, which calls _load_governance_principles in its __init__
        settings_instance = self.get_settings_instance()

        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES, valid_principles_data["principles"])
        mock_file_open.assert_called_once_with(GOVERNANCE_FILE_PATH, 'r', encoding='utf-8')
        mock_yaml_load.assert_called_once()

    @patch('src.config.settings.Path.exists')
    @patch('logging.Logger.warning') # Patch the logger used in settings.py
    def test_scenario_2_governance_yaml_not_found(self, mock_logger_warning, mock_path_exists):
        mock_path_exists.return_value = False # governance.yaml does NOT exist
        
        settings_instance = self.get_settings_instance()

        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES, [])
        # Check if logger.warning was called with a message containing "not found"
        found_log = False
        for call_args in mock_logger_warning.call_args_list:
            if "not found" in call_args[0][0]:
                found_log = True
                break
        self.assertTrue(found_log, "Warning log for file not found was not emitted.")


    @patch('src.config.settings.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    @patch('logging.Logger.error') # Patch the logger
    def test_scenario_3_invalid_yaml_format(self, mock_logger_error, mock_yaml_load, mock_file_open, mock_path_exists):
        mock_path_exists.return_value = True
        mock_yaml_load.side_effect = yaml.YAMLError("Simulated YAML parsing error")
        
        settings_instance = self.get_settings_instance()

        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES, [])
        # Check if logger.error was called with a message containing "Error decoding YAML"
        found_log = False
        for call_args in mock_logger_error.call_args_list:
            if "Error decoding YAML" in call_args[0][0]:
                found_log = True
                break
        self.assertTrue(found_log, "Error log for YAML decoding error was not emitted.")

    @patch('src.config.settings.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    @patch('logging.Logger.error')
    def test_scenario_4a_missing_principles_key(self, mock_logger_error, mock_yaml_load, mock_file_open, mock_path_exists):
        mock_path_exists.return_value = True
        mock_yaml_load.return_value = {"not_principles": []} # Missing 'principles' key
        
        settings_instance = self.get_settings_instance()

        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES, [])
        found_log = False
        for call_args in mock_logger_error.call_args_list:
            if "has incorrect structure" in call_args[0][0]:
                found_log = True
                break
        self.assertTrue(found_log, "Error log for incorrect structure (missing 'principles' key) not emitted.")

    @patch('src.config.settings.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    @patch('logging.Logger.error')
    def test_scenario_4b_principles_not_a_list(self, mock_logger_error, mock_yaml_load, mock_file_open, mock_path_exists):
        mock_path_exists.return_value = True
        mock_yaml_load.return_value = {"principles": "this is not a list"}
        
        settings_instance = self.get_settings_instance()

        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES, [])
        found_log = False
        for call_args in mock_logger_error.call_args_list:
            if "has incorrect structure" in call_args[0][0]: # Same error message as 4a
                found_log = True
                break
        self.assertTrue(found_log, "Error log for incorrect structure ('principles' not a list) not emitted.")


    @patch('src.config.settings.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    @patch('logging.Logger.warning') # Check for warnings about malformed entries
    def test_scenario_5_malformed_principle_entries(self, mock_logger_warning, mock_yaml_load, mock_file_open, mock_path_exists):
        mock_path_exists.return_value = True
        principles_data_malformed = {
            "principles": [
                {"id": "GP001", "name": "Valid Principle", "text": "Valid text.", "applies_to": ["all_agents"], "enabled": True},
                {"id": "GP002", "name": "Missing Text Key", "applies_to": ["worker"], "enabled": True}, # Missing 'text'
                {"id": "GP003", "text": "Missing Name Key", "applies_to": ["pm"], "enabled": True} # Missing 'name'
            ]
        }
        mock_yaml_load.return_value = principles_data_malformed
        
        settings_instance = self.get_settings_instance()

        # The current implementation of _load_governance_principles logs a warning but still includes malformed dicts.
        # Let's verify that all dicts are loaded, and that warnings were logged.
        self.assertEqual(len(settings_instance.GOVERNANCE_PRINCIPLES), 3) 
        self.assertEqual(settings_instance.GOVERNANCE_PRINCIPLES[0]["name"], "Valid Principle")
        
        # Check for specific warnings for GP002 and GP003
        warning_logs = [call_args[0][0] for call_args in mock_logger_warning.call_args_list]
        self.assertTrue(any("missing required keys: GP002" in log for log in warning_logs))
        self.assertTrue(any("missing required keys: GP003" in log for log in warning_logs))


if __name__ == '__main__':
    unittest.main()
