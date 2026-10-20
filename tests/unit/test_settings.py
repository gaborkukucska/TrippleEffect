# START OF FILE tests/unit/test_settings.py
"""
Tests for Settings._load_governance_principles() and usable_governance_principles().

Round 2m: the old tests patched builtins.open and yaml.safe_load for a whole Settings() construction, so prompts.yaml
was "read" through the same mocks (open was called twice), and they started module-level patches of config_manager and
ModelRegistryClass at import time, which leaked into every test module collected after them. These tests write a real
governance file into a temporary directory, point GOVERNANCE_FILE_PATH at it and run only the loader.

Rules under test:
* missing file -> [] and a "not found" warning; invalid YAML -> [] and an "Error decoding YAML" error;
  no 'principles' list -> [] and a "has incorrect structure" error;
* entries that are not dicts or lack 'name' or 'text' are dropped with a warning (they used to make every system prompt
  fail, and a non-dict entry used to empty the whole list); entries lacking only 'id', 'applies_to' or 'enabled' are
  kept with a warning;
* the real governance.yaml loads completely.
"""
import logging
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.config import settings as settings_module
from src.config.settings import GOVERNANCE_FILE_PATH, Settings, usable_governance_principles

VALID = [
    {"id": "GP001", "name": "Test Principle 1", "text": "Text 1", "applies_to": ["all_agents"], "enabled": True},
    {"id": "GP002", "name": "Test Principle 2", "text": "Text 2", "applies_to": ["worker"], "enabled": False},
]


class TestGovernanceLoading(unittest.TestCase):

    def setUp(self):
        # other test modules disable logging globally; assertLogs needs it on
        self._previous_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, self._previous_disable)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "governance.yaml"
        patcher = patch.object(settings_module, "GOVERNANCE_FILE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self, text=None):
        if text is not None:
            self.path.write_text(text, encoding="utf-8")
        holder = types.SimpleNamespace()
        Settings._load_governance_principles(holder)
        return holder.GOVERNANCE_PRINCIPLES

    def test_valid_file(self):
        self.assertEqual(self.load(yaml.safe_dump({"principles": VALID})), VALID)

    def test_file_not_found(self):
        with self.assertLogs("src.config.settings", level="WARNING") as logs:
            self.assertEqual(self.load(), [])
        self.assertTrue(any("not found" in line for line in logs.output))

    def test_invalid_yaml(self):
        with self.assertLogs("src.config.settings", level="ERROR") as logs:
            self.assertEqual(self.load("principles: [unclosed"), [])
        self.assertTrue(any("Error decoding YAML" in line for line in logs.output))

    def test_missing_principles_key(self):
        with self.assertLogs("src.config.settings", level="ERROR") as logs:
            self.assertEqual(self.load(yaml.safe_dump({"not_principles": []})), [])
        self.assertTrue(any("has incorrect structure" in line for line in logs.output))

    def test_principles_not_a_list(self):
        with self.assertLogs("src.config.settings", level="ERROR") as logs:
            self.assertEqual(self.load(yaml.safe_dump({"principles": "this is not a list"})), [])
        self.assertTrue(any("has incorrect structure" in line for line in logs.output))

    def test_malformed_entries(self):
        entries = [
            {"id": "GP001", "name": "Valid Principle", "text": "Valid text.", "applies_to": ["all_agents"], "enabled": True},
            {"id": "GP002", "name": "Missing Text Key", "applies_to": ["worker"], "enabled": True},
            {"id": "GP003", "text": "Missing Name Key", "applies_to": ["pm"], "enabled": True},
            "just a string",
            {"id": "GP005", "name": "No Flag", "text": "Kept, disabled by default.", "applies_to": ["pm"]},
        ]
        with self.assertLogs("src.config.settings", level="WARNING") as logs:
            loaded = self.load(yaml.safe_dump({"principles": entries}))
        self.assertEqual([p["id"] for p in loaded], ["GP001", "GP005"])
        output = "\n".join(logs.output)
        self.assertIn("Governance principle GP002 is missing required keys: text. It is ignored.", output)
        self.assertIn("Governance principle GP003 is missing required keys: name. It is ignored.", output)
        self.assertIn("Governance principle at index 3 is not a dictionary", output)
        self.assertIn("Governance principle GP005 is missing required keys: enabled. It is kept.", output)

    def test_usable_principles_without_logging_setup(self):
        logging.disable(logging.CRITICAL)
        self.assertEqual(usable_governance_principles([]), [])
        self.assertEqual(usable_governance_principles([None, {"name": "N"}, VALID[0]]), [VALID[0]])


class TestRealGovernanceFile(unittest.TestCase):

    def test_real_governance_yaml_loads_completely(self):
        self.assertTrue(GOVERNANCE_FILE_PATH.exists())
        raw = yaml.safe_load(GOVERNANCE_FILE_PATH.read_text(encoding="utf-8"))["principles"]
        holder = types.SimpleNamespace()
        Settings._load_governance_principles(holder)
        self.assertEqual(holder.GOVERNANCE_PRINCIPLES, raw)
        self.assertTrue(raw)


if __name__ == '__main__':
    unittest.main()
