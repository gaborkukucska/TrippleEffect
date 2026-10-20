# START OF FILE tests/tools/test_knowledge_base_tool.py
"""
Tests for KnowledgeBaseTool, action 'search_agent_thoughts'.

Round 2m: these tests never ran - they were 'async def' methods of a plain unittest.TestCase, so pytest only created the
coroutines ("coroutine ... was never awaited") and counted them as passed. They also expected an older text result. The
tool returns a dict: {"status", "message", "thoughts": [{"id", "timestamp", "keywords", "thought"}]}.

Rules under test:
* the search keywords are 'agent_thought', the agent id and the comma-separated additional keywords;
* max_results is converted to int (default 5; an invalid value keeps 5);
* a missing agent_identifier is an error and nothing is searched;
* each thought gives id, created_at as 'YYYY-MM-DD HH:MM:SS' ('N/A' if missing), keywords and the full summary;
* the near-miss actions 'search_thoughts' and 'get_thoughts' are refused with the suggestion 'search_agent_thoughts'.
"""
import datetime
import logging
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.tools import knowledge_base
from src.tools.knowledge_base import KnowledgeBaseTool


def _thought(item_id, keywords, summary, created_at):
    return types.SimpleNamespace(id=item_id, keywords=keywords, summary=summary, created_at=created_at,
                                 importance_score=0.5)


class TestSearchAgentThoughts(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.db = patch.object(knowledge_base, "db_manager", new_callable=AsyncMock).start()
        self.addCleanup(patch.stopall)
        self.db.search_knowledge.return_value = []
        self.tool = KnowledgeBaseTool()

    async def run_tool(self, **kwargs):
        return await self.tool.execute(agent_id="caller", agent_sandbox_path=Path("/tmp/unused_sandbox"), **kwargs)

    async def test_basic_search(self):
        await self.run_tool(action="search_agent_thoughts", agent_identifier="test_agent_001", max_results="3")
        self.db.search_knowledge.assert_awaited_once_with(query_keywords=["agent_thought", "test_agent_001"], max_results=3)

    async def test_additional_keywords(self):
        await self.run_tool(action="search_agent_thoughts", agent_identifier=" test_agent_002 ",
                            additional_keywords="project_alpha, planning,,", max_results=5)
        self.db.search_knowledge.assert_awaited_once_with(
            query_keywords=["agent_thought", "test_agent_002", "project_alpha", "planning"], max_results=5)

    async def test_default_and_invalid_max_results(self):
        await self.run_tool(action="search_agent_thoughts", agent_identifier="A")
        await self.run_tool(action="search_agent_thoughts", agent_identifier="A", max_results="many")
        self.assertEqual([c.kwargs["max_results"] for c in self.db.search_knowledge.await_args_list], [5, 5])

    async def test_missing_agent_identifier(self):
        result = await self.run_tool(action="search_agent_thoughts")
        self.assertEqual(result, {"status": "error",
                                  "message": "'agent_identifier' parameter is required for 'search_agent_thoughts'."})
        self.db.search_knowledge.assert_not_awaited()

    async def test_results(self):
        long_summary = "This is a very long thought that exceeds two hundred characters. " * 5
        self.db.search_knowledge.return_value = [
            _thought(101, "agent_thought,test_agent_003,planning", "Plan the alpha phase.",
                     datetime.datetime(2023, 10, 26, 10, 30, 0)),
            _thought(102, "agent_thought,test_agent_003,long", long_summary, None),
        ]
        result = await self.run_tool(action="search_agent_thoughts", agent_identifier="test_agent_003", max_results="2")
        self.assertEqual(result, {"status": "success", "message": "Found 2 thought(s) for agent 'test_agent_003'.",
                                  "thoughts": [
                                      {"id": 101, "timestamp": "2023-10-26 10:30:00",
                                       "keywords": "agent_thought,test_agent_003,planning", "thought": "Plan the alpha phase."},
                                      {"id": 102, "timestamp": "N/A", "keywords": "agent_thought,test_agent_003,long",
                                       "thought": long_summary},
                                  ]})

    async def test_no_results(self):
        result = await self.run_tool(action="search_agent_thoughts", agent_identifier="nobody")
        self.assertEqual(result, {"status": "success",
                                  "message": "No thoughts found for agent 'nobody' matching keywords.", "thoughts": []})

    async def test_near_miss_actions_get_a_suggestion(self):
        for action in ("search_thoughts", "get_thoughts"):
            with self.subTest(action=action):
                result = await self.run_tool(action=action, agent_identifier="A")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["suggested_action"], "search_agent_thoughts")
        self.db.search_knowledge.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
