import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
import datetime

# Import the class to be tested
from src.tools.knowledge_base import KnowledgeBaseTool
# Import the structure that search_knowledge is expected to return (if available and specific)
# For now, we'll use MagicMock for returned items.
# from src.core.database_schema import KnowledgeItem # Assuming this path if it exists

# Disable most logging output for tests unless specifically testing logging
import logging
logging.disable(logging.CRITICAL)

class TestKnowledgeBaseToolSearchAgentThoughts(unittest.TestCase):

    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock)
    def setUp(self, mock_db_manager_for_setup): # Mocked here to allow instantiation
        self.db_manager_mock = mock_db_manager_for_setup # Store mock for use in tests
        self.knowledge_base_tool = KnowledgeBaseTool()
        # Common dummy args for tool execution not directly tested here
        self.dummy_agent_id = "test_caller_agent"
        self.dummy_sandbox_path = Path("/tmp/dummy_sandbox")


    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock) # Patch for the specific test method
    async def test_search_agent_thoughts_basic(self, mock_db_manager):
        mock_db_manager.search_knowledge.return_value = [] # Return empty list to simplify
        
        agent_id_to_search = "test_agent_001"
        max_results_expected = 3

        await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts",
            agent_identifier=agent_id_to_search,
            max_results=str(max_results_expected) # Params often come as strings from LLM
        )

        mock_db_manager.search_knowledge.assert_called_once()
        call_args = mock_db_manager.search_knowledge.call_args
        
        # Check query_keywords
        self.assertIn("agent_thought", call_args.kwargs['query_keywords'])
        self.assertIn(agent_id_to_search, call_args.kwargs['query_keywords'])
        self.assertEqual(len(call_args.kwargs['query_keywords']), 2) # Only two keywords
        
        # Check max_results
        self.assertEqual(call_args.kwargs['max_results'], max_results_expected)
        self.assertIsNone(call_args.kwargs.get('min_importance')) # Should be None by default for thoughts

    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock)
    async def test_search_agent_thoughts_with_additional_keywords(self, mock_db_manager):
        mock_db_manager.search_knowledge.return_value = []
        
        agent_id_to_search = "test_agent_002"
        additional_keywords_str = "project_alpha,planning"
        expected_additional_keywords = ["project_alpha", "planning"]
        max_results_expected = 5

        await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts",
            agent_identifier=agent_id_to_search,
            additional_keywords=additional_keywords_str,
            max_results=str(max_results_expected)
        )

        mock_db_manager.search_knowledge.assert_called_once()
        call_args = mock_db_manager.search_knowledge.call_args
        
        self.assertIn("agent_thought", call_args.kwargs['query_keywords'])
        self.assertIn(agent_id_to_search, call_args.kwargs['query_keywords'])
        for kw in expected_additional_keywords:
            self.assertIn(kw, call_args.kwargs['query_keywords'])
        self.assertEqual(len(call_args.kwargs['query_keywords']), 2 + len(expected_additional_keywords))
        
        self.assertEqual(call_args.kwargs['max_results'], max_results_expected)

    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock)
    async def test_search_agent_thoughts_missing_agent_identifier(self, mock_db_manager):
        result = await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts"
            # agent_identifier is missing
        )
        
        self.assertIn("Error: 'agent_identifier' parameter is required", result)
        mock_db_manager.search_knowledge.assert_not_called()

    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock)
    async def test_search_agent_thoughts_formatting_of_results(self, mock_db_manager):
        # Prepare mock KnowledgeItem-like objects
        mock_item1 = MagicMock()
        mock_item1.id = 101
        mock_item1.importance_score = 0.85 # Though not used in current thought search, good to have
        mock_item1.keywords = "agent_thought,test_agent_003,planning"
        mock_item1.summary = "This is the first thought about planning the alpha phase."
        mock_item1.timestamp = datetime.datetime(2023, 10, 26, 10, 30, 0)

        mock_item2 = MagicMock()
        mock_item2.id = 102
        mock_item2.importance_score = 0.75
        mock_item2.keywords = "agent_thought,test_agent_003,coding,fix"
        mock_item2.summary = "Second thought: The coding part needs a quick fix for the UI bug identified yesterday."
        mock_item2.timestamp = datetime.datetime(2023, 10, 27, 11, 0, 0)
        
        mock_db_manager.search_knowledge.return_value = [mock_item1, mock_item2]
        
        agent_id_to_search = "test_agent_003"
        result = await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts",
            agent_identifier=agent_id_to_search,
            max_results="2"
        )

        self.assertIn(f"Found 2 thought(s) for agent '{agent_id_to_search}'", result)
        self.assertIn(f"ID: {mock_item1.id}", result)
        self.assertIn(f"Saved: {mock_item1.timestamp.strftime('%Y-%m-%d %H:%M:%S')}", result)
        self.assertIn(f"Keywords: '{mock_item1.keywords}'", result)
        self.assertIn(f"Thought: {mock_item1.summary}", result)
        
        self.assertIn(f"ID: {mock_item2.id}", result)
        self.assertIn(f"Saved: {mock_item2.timestamp.strftime('%Y-%m-%d %H:%M:%S')}", result)
        self.assertIn(f"Keywords: '{mock_item2.keywords}'", result)
        self.assertIn(f"Thought: {mock_item2.summary}", result)
        
        # Check that summary is truncated if longer than MAX_SUMMARY_LEN (200)
        long_summary = "This is a very long thought that definitely exceeds the two hundred character limit. " * 5
        mock_item_long = MagicMock()
        mock_item_long.id = 103
        mock_item_long.keywords = "agent_thought,test_agent_003,long"
        mock_item_long.summary = long_summary
        mock_item_long.timestamp = datetime.datetime.now()
        mock_db_manager.search_knowledge.return_value = [mock_item_long]

        result_long = await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts",
            agent_identifier=agent_id_to_search,
            max_results="1"
        )
        expected_truncated_summary = long_summary[:200] + "..."
        self.assertIn(f"Thought: {expected_truncated_summary}", result_long)


    @patch('src.tools.knowledge_base.db_manager', new_callable=AsyncMock)
    async def test_search_agent_thoughts_no_results(self, mock_db_manager):
        mock_db_manager.search_knowledge.return_value = [] # No items found
        
        agent_id_to_search = "test_agent_no_thoughts"
        result = await self.knowledge_base_tool.execute(
            agent_id=self.dummy_agent_id,
            agent_sandbox_path=self.dummy_sandbox_path,
            action="search_agent_thoughts",
            agent_identifier=agent_id_to_search
        )
        
        self.assertIn(f"No thoughts found for agent '{agent_id_to_search}'", result)

if __name__ == '__main__':
    unittest.main()
