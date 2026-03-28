"""
Tests for <tool_call> JSON format parsing in agent_tool_parser.py

Verifies that the parser correctly handles the <tool_call>{"name":"...", "arguments":{...}}</tool_call>
format used by qwen3 and similar models, in addition to the standard XML format.
"""
import unittest
import re
import logging
from unittest.mock import MagicMock

from src.agents.agent_tool_parser import (
    find_and_parse_xml_tool_calls,
    _parse_tool_call_json_blocks,
    TOOL_CALL_JSON_PATTERN,
)
from src.tools.base import BaseTool

logging.getLogger().setLevel(logging.DEBUG)


def _make_mock_tools(*names):
    """Create a dict of mock tools with the given names."""
    tools = {}
    for name in names:
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.get_schema.return_value = {
            "name": name,
            "parameters": [
                {"name": "action", "type": "string", "required": True},
            ],
        }
        tools[name] = mock_tool
    return tools


# Precompile the XML patterns used by Agent Core for testing
_TOOL_NAMES = ["manage_team", "send_message", "project_management", "file_system"]
_SAFE_NAMES = [re.escape(n.lower()) for n in _TOOL_NAMES]
_PATTERN_GROUP = "|".join(_SAFE_NAMES)
_RAW_PATTERN = re.compile(
    rf"<({_PATTERN_GROUP})>([\s\S]*?)</\1>", re.IGNORECASE | re.DOTALL
)
_MD_PATTERN = re.compile(
    rf"```(?:[a-zA-Z]*\n)?\s*(<({_PATTERN_GROUP})>[\s\S]*?</\2>)\s*\n?```",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


class TestToolCallJSONParsing(unittest.TestCase):
    """Tests for the <tool_call> JSON format parser."""

    def setUp(self):
        self.tools = _make_mock_tools(*_TOOL_NAMES)

    def test_single_valid_tool_call(self):
        """A single well-formed <tool_call> block is parsed correctly."""
        buffer = '<tool_call>{"name": "manage_team", "arguments": {"action": "create_agent"}}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)
        self.assertEqual(len(result["parsing_errors"]), 0)
        name, args, _ = result["valid_calls"][0]
        self.assertEqual(name, "manage_team")
        self.assertEqual(args["action"], "create_agent")

    def test_multiple_tool_calls(self):
        """Multiple <tool_call> blocks are all parsed."""
        buffer = (
            '<tool_call>{"name": "manage_team", "arguments": {"action": "create_agent"}}</tool_call>\n'
            '<tool_call>{"name": "send_message", "arguments": {"action": "send"}}</tool_call>'
        )
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 2)
        self.assertEqual(result["valid_calls"][0][0], "manage_team")
        self.assertEqual(result["valid_calls"][1][0], "send_message")

    def test_unknown_tool_skipped(self):
        """An unknown tool name in <tool_call> is skipped (no error, no valid call)."""
        buffer = '<tool_call>{"name": "nonexistent_tool", "arguments": {"action": "test"}}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 0)
        self.assertEqual(len(result["parsing_errors"]), 0)

    def test_invalid_json_reports_error(self):
        """Malformed JSON inside <tool_call> yields a parsing error."""
        buffer = '<tool_call>{"name": "manage_team", "arguments": {INVALID}}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 0)
        self.assertEqual(len(result["parsing_errors"]), 1)
        self.assertIn("JSON parse error", result["parsing_errors"][0]["error_message"])

    def test_missing_name_field_reports_error(self):
        """<tool_call> JSON without a 'name' field yields a parsing error."""
        buffer = '<tool_call>{"arguments": {"action": "test"}}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 0)
        self.assertEqual(len(result["parsing_errors"]), 1)

    def test_xml_takes_priority_over_json(self):
        """Standard XML tool calls are found first; JSON fallback not triggered."""
        buffer = '<manage_team><action>create_agent</action></manage_team>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)
        name, args, _ = result["valid_calls"][0]
        self.assertEqual(name, "manage_team")
        self.assertEqual(args["action"], "create_agent")

    def test_json_fallback_when_no_xml(self):
        """JSON format is used only when XML format finds nothing."""
        buffer = (
            "I will create an agent now.\n"
            '<tool_call>{"name": "manage_team", "arguments": {"action": "create_agent"}}</tool_call>'
        )
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)
        self.assertEqual(result["valid_calls"][0][0], "manage_team")

    def test_case_insensitive_tool_name(self):
        """Tool name matching is case-insensitive."""
        buffer = '<tool_call>{"name": "Manage_Team", "arguments": {"action": "list"}}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)
        self.assertEqual(result["valid_calls"][0][0], "manage_team")

    def test_whitespace_in_tool_call_block(self):
        """Whitespace around JSON inside <tool_call> is handled."""
        buffer = '<tool_call>  \n  {"name": "manage_team", "arguments": {"action": "test"}}  \n  </tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)

    def test_empty_buffer_returns_empty(self):
        """Empty buffer returns no calls and no errors."""
        result = find_and_parse_xml_tool_calls(
            "", self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 0)
        self.assertEqual(len(result["parsing_errors"]), 0)

    def test_tool_call_json_pattern_regex(self):
        """The TOOL_CALL_JSON_PATTERN regex correctly matches <tool_call> blocks."""
        text = '<tool_call>{"name": "test", "arguments": {}}</tool_call>'
        match = TOOL_CALL_JSON_PATTERN.search(text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), '{"name": "test", "arguments": {}}')

    def test_non_dict_arguments_wrapped(self):
        """Non-dict arguments are wrapped into a dict with key 'value'."""
        buffer = '<tool_call>{"name": "manage_team", "arguments": "just a string"}</tool_call>'
        result = find_and_parse_xml_tool_calls(
            buffer, self.tools, _RAW_PATTERN, _MD_PATTERN, "test_agent"
        )
        self.assertEqual(len(result["valid_calls"]), 1)
        self.assertEqual(result["valid_calls"][0][1], {"value": "just a string"})


class TestParseToolCallJsonBlocksDirect(unittest.TestCase):
    """Direct tests for _parse_tool_call_json_blocks helper function."""

    def setUp(self):
        self.tools = _make_mock_tools("manage_team", "send_message")

    def test_no_tool_call_tags(self):
        """Plain text without <tool_call> returns empty lists."""
        calls, errors = _parse_tool_call_json_blocks(
            "Hello there, no tools here.", self.tools, set(), "agent1"
        )
        self.assertEqual(len(calls), 0)
        self.assertEqual(len(errors), 0)

    def test_overlapping_spans_skipped(self):
        """Already-processed spans are skipped."""
        buffer = '<tool_call>{"name": "manage_team", "arguments": {}}</tool_call>'
        # Pre-populate with span covering the entire buffer
        existing_spans = {(0, len(buffer))}
        calls, errors = _parse_tool_call_json_blocks(
            buffer, self.tools, existing_spans, "agent1"
        )
        self.assertEqual(len(calls), 0)
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
