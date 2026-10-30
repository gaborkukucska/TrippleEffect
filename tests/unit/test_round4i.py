# START OF FILE tests/unit/test_round4i.py
"""
Round 4i: the run of 2026-10-30 10:20-11:08 (qwen3.5:4b-q4_K_M, WiFi_Snake_Game: Python / Flask; Ollama 0.40.13).

* 10:38:20-10:39:22 and 10:55:10-10:56:25 five replies of W1 in a row ended in "XML syntax error on line 12 (12, 12, 29,
  262): unexpected EOF": Ollama reads a qwen tool call as XML, and a <parameter=...> that is ended by </function> without its
  </parameter> no longer parses (before 0.40.11: "element <parameter> closed by </function>"). At temperature 0.1 each retry
  repeated the reply, and the general notice did not name the mistake.
* The fifth failed W1 over to qwen3.5:27b - the largest model on the one GPU server: prompts took 25 s, other agents'
  requests timed out at the proxy (524), and W1 made six tool calls in 28 minutes.
* 10:41:23-10:44:19 a 27B reply ended without Ollama's "done" and carried nothing: it counted as W1's second EMPTY reply,
  and the Guardian cleared W1's history and put it in worker_wait in the middle of its task.
"""
import asyncio
import logging
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROVIDER = "ollama-local-oli-gaborkukucska-com"
XML_EOF = "[Ollama Error]: XML syntax error on line 12: unexpected EOF"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ======================================================================================== 1. the retry is not the same reply
def test_a_retry_after_unusable_replies_is_warmer():
    from src.agents.core import retry_temperature
    assert retry_temperature(0.1, 0) == 0.1
    assert [retry_temperature(0.1, n) for n in (1, 2, 3, 4, 9)] == [0.5, 0.7, 0.9, 0.9, 0.9]
    assert retry_temperature(0.8, 1) == 0.8          # never colder than the agent's own
    assert retry_temperature(0.1, None) == 0.1 and retry_temperature(0.1, True) == 0.1
    source = (Path(__file__).resolve().parents[2] / "src" / "agents" / "core.py").read_text(encoding="utf-8")
    assert "messages=history_to_use, model=self.model, temperature=call_temperature," in source


def test_the_notice_names_the_unclosed_parameter():
    from src.agents.cycle_handler import malformed_reply_notice
    agent = types.SimpleNamespace(_last_reply_malformed={"kind": "tool_call", "detail": XML_EOF},
                                  _consecutive_malformed_replies=2)
    notice = malformed_reply_notice(agent)
    assert notice.startswith("[Framework Notice]: Your last reply was unusable (2 time(s) in a row), so NOTHING was "
                             f"executed or saved: the model server could not read its tool call ({XML_EOF}): a parameter "
                             "was not closed. In a tool call EVERY parameter ends with its own </parameter> line - also the "
                             "LAST one, before </function>:")
    assert "<parameter=NAME>\nVALUE\n</parameter>\n</function>\n</tool_call>" in notice
    agent._last_reply_malformed["detail"] = ("[Ollama Error]: XML syntax error on line 37: element <parameter> closed by "
                                             "</function>")
    assert "a parameter was not closed" in malformed_reply_notice(agent)
    agent._last_reply_malformed["detail"] = "[Ollama Error]: error parsing tool call: invalid character"
    assert "a parameter was not closed" not in malformed_reply_notice(agent)


# ======================================================================================== 2. failover: a model of about its size
class TestFailoverToAModelOfItsSize:
    def setup_method(self):
        from src.agents import failover_handler
        self.fh = failover_handler
        failover_handler._models_without_tool_support.clear()
        self.agent = types.SimpleNamespace(
            agent_id="W1", agent_type="worker", provider_name=PROVIDER, model="qwen3.5:4b-q4_K_M",
            agent_config={"config": {}}, _failover_state={}, set_status=MagicMock())
        tracker = MagicMock()
        tracker.get_metrics.return_value = None
        self.manager = types.SimpleNamespace(agents={"W1": self.agent}, send_to_ui=AsyncMock(), performance_tracker=tracker)
        self.patches = [patch.object(failover_handler, "model_registry"),
                        patch.object(failover_handler, "_check_provider_health", new_callable=AsyncMock),
                        patch.object(failover_handler, "_try_switch_agent", new_callable=AsyncMock)]
        self.registry, self.health, self.switch = (p.start() for p in self.patches)

        def model(mid, size):
            return {"id": mid, "num_parameters": size, "provider": PROVIDER, "model_capabilities": ["completion", "tools"]}
        self.registry.get_available_models_dict.return_value = {PROVIDER: [
            model("qwen3.5:4b-q4_K_M", 4.66e9), model("qwen3.5:9b-q8_0", 9.65e9), model("qwen3.5:27b-q4_K_M", 27.8e9),
            model("gpt-oss:20b", 20.9e9)]}
        self.registry.get_reachable_provider_url.side_effect = lambda provider: "https://oli.gaborkukucska.com"
        self.registry.get_model_info.return_value = None
        self.health.return_value = True
        self.switch.return_value = True

    def teardown_method(self):
        for p in self.patches:
            p.stop()
        self.fh._models_without_tool_support.clear()

    def test_not_the_largest_model_of_the_server(self):
        with quiet():
            switched = run(self.fh.handle_agent_model_failover(self.manager, "W1", ValueError(XML_EOF)))     # 10:39:22
        assert switched is True and [(c.args[2], c.args[3]) for c in self.switch.call_args_list] == [
            (PROVIDER, "qwen3.5:9b-q8_0")]

    def test_the_order(self):
        sizes = [{"id": "27b", "num_parameters_sortable": 27.8e9}, {"id": "20b", "num_parameters_sortable": 20.9e9},
                 {"id": "9b", "num_parameters_sortable": 9.65e9}, {"id": "e4b", "num_parameters_sortable": 8e9},
                 {"id": "unknown", "num_parameters_sortable": 0}]
        assert [m["id"] for m in self.fh.size_capped_order(sizes, 4.66e9)] == ["9b", "e4b", "27b", "20b", "unknown"]
        assert self.fh.size_capped_order(sizes, None) == sizes
        assert self.fh.model_size(self.registry.get_available_models_dict.return_value, "qwen3.5:9b-q8_0") == 9.65e9
        assert self.fh.model_size({}, "x") is None


# ======================================================================================== 3. a reply cut before it was done
def test_a_reply_that_ends_before_done_and_carries_nothing_is_a_connection_error():
    from src.agents.failover_handler import is_provider_level_error
    from src.llm_providers.ollama_provider import complete_reply_events
    with quiet():
        cut = complete_reply_events({"message": {"role": "assistant", "content": ""}, "done": False})       # 10:44:19
    assert len(cut) == 1 and cut[0]["type"] == "error"
    assert cut[0]["content"].startswith("[Ollama Error]: Connection ended early")
    assert is_provider_level_error(cut[0]["_exception_obj"])
    with quiet():
        # a finished empty reply stays an empty reply; a cut reply that carried text or a tool call is passed on
        assert complete_reply_events({"message": {"role": "assistant", "content": ""}, "done": True}) == []
        assert [e["type"] for e in complete_reply_events({"message": {"role": "assistant", "content": "Done."},
                                                          "done": False})] == ["response_chunk"]
        assert [e["type"] for e in complete_reply_events({"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "file_system", "arguments": {}}}]}, "done": False})] == ["native_tool_calls"]


def test_the_outcome_of_a_cut_reply_is_provider_level():
    from src.agents.cycle_components.outcome_determiner import CycleOutcomeDeterminer
    from src.llm_providers.ollama_provider import complete_reply_events
    with quiet():
        event = complete_reply_events({"message": {"role": "assistant", "content": ""}, "done": False})[0]
    context = types.SimpleNamespace(agent=types.SimpleNamespace(agent_id="W1"), last_error_obj=event["_exception_obj"],
                                    last_error_content=event["content"], is_retryable_error_type=None,
                                    is_key_related_error=None, is_provider_level_error=False, trigger_failover=False,
                                    cycle_completed_successfully=False)
    with quiet():
        CycleOutcomeDeterminer().determine_cycle_outcome(context)
    # not the agent's (empty) reply: the Guardian does not count it, the agent keeps its model and its history
    assert context.is_provider_level_error is True

# END OF FILE tests/unit/test_round4i.py
