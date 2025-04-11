# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
# import xml.etree.ElementTree as ET
import html # For unescaping
import logging

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Agent Status Constants (Removed AWAITING_USER_OVERRIDE)
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# XML Tool Call Patterns (compiled in __init__)
XML_TOOL_CALL_PATTERN = None
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```"


class Agent:
    """ Represents an individual LLM agent. """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider,
        manager: 'AgentManager'
        ):
        """ Initializes an Agent instance. """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        self.final_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        if not self.final_system_prompt: logger.error(f"Agent {self.agent_id}: 'system_prompt' is missing!"); self.final_system_prompt = "You are a helpful assistant."
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.agent_config: Dict[str, Any] = agent_config
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        logger.debug(f"Agent {self.agent_id} system prompt (first 500):\n{self.final_system_prompt[:500]}...")
        self.status: str = AGENT_STATUS_IDLE
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.message_history: List[MessageDict] = [{"role": "system", "content": self.final_system_prompt}]
        self.text_buffer: str = ""
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        self.raw_xml_tool_call_pattern = None; self.markdown_xml_tool_call_pattern = None
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                 safe_tool_names = [re.escape(name) for name in tool_names]; tool_names_pattern_group = '|'.join(safe_tool_names)
                 raw_pattern_str = rf"<({tool_names_pattern_group})>([\s\S]*?)</\1>"; self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)
                 md_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group); self.markdown_xml_tool_call_pattern = re.compile(md_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)
                 logger.info(f"Agent {self.agent_id}: Compiled XML tool patterns: {tool_names}")
            else: logger.info(f"Agent {self.agent_id}: No tools found.")
        else: logger.warning(f"Agent {self.agent_id}: Manager/ToolExecutor not available.")
        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}.")

    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        if self.status != new_status: logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else: logger.warning(f"Agent {self.agent_id}: Manager not set.")

    def set_manager(self, manager: 'AgentManager'): self.manager = manager
    def set_tool_executor(self, tool_executor: Any): logger.warning(f"Agent {self.agent_id}: set_tool_executor called but not used."); pass

    def ensure_sandbox_exists(self) -> bool:
        try: self.sandbox_path.mkdir(parents=True, exist_ok=True); return True
        except OSError as e: logger.error(f"Error creating sandbox for Agent {self.agent_id} at {self.sandbox_path}: {e}"); return False
        except Exception as e: logger.error(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}", exc_info=True); return False

    # --- *** CORRECTED _find_and_parse_tool_calls *** ---
    def _find_and_parse_tool_calls(self) -> List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
        """ Finds and parses *all* valid XML tool calls (raw or fenced). """
        if not self.text_buffer: return []
        buffer_content = self.text_buffer
        logger.debug(f"Agent {self.agent_id}: Checking buffer for tool calls (Len: {len(buffer_content)}): '{buffer_content[-500:]}'")
        found_calls = []; processed_spans = set()

        # Helper Function (indented within the method)
        def parse_single_match(match, is_markdown):
             match_start, match_end = match.span()
             for proc_start, proc_end in processed_spans:
                  if max(match_start, proc_start) < min(match_end, proc_end): logger.debug(f"Skipping overlapping match at ({match_start}, {match_end})"); return None
             xml_block_to_parse = ""; tool_name_from_outer_match = ""
             if is_markdown: xml_block_to_parse = match.group(1).strip(); tool_name_from_outer_match = match.group(2)
             else: xml_block_to_parse = match.group(0).strip(); tool_name_from_outer_match = match.group(1)
             try:
                 # Ensure regex patterns are compiled before use
                 if not self.raw_xml_tool_call_pattern:
                     logger.error("Raw XML tool call pattern not compiled!"); return None
                 tool_name = next((name for name in self.manager.tool_executor.tools if name.lower() == tool_name_from_outer_match.lower()), None)
                 if not tool_name: logger.warning(f"Agent {self.agent_id}: Found <{tool_name_from_outer_match}> but no matching tool registered."); return None
                 inner_match = self.raw_xml_tool_call_pattern.search(xml_block_to_parse)
                 if not inner_match: logger.warning(f"Agent {self.agent_id}: Could not parse inner content: '{xml_block_to_parse}'"); return None
                 inner_tool_name, inner_content = inner_match.groups()
                 if inner_tool_name.lower() != tool_name.lower(): logger.warning(f"Agent {self.agent_id}: Tool name mismatch ({tool_name} vs {inner_tool_name})."); return None
                 logger.info(f"Agent {self.agent_id}: Detected call for tool '{tool_name}' at span ({match_start}, {match_end}) (MD: {is_markdown})")
                 tool_args = {}; param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"; param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                 for param_name, param_value_escaped in param_matches: tool_args[param_name] = html.unescape(param_value_escaped.strip())
                 logger.info(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}"); processed_spans.add((match_start, match_end)); return tool_name, tool_args, (match_start, match_end)
             except Exception as parse_err: logger.error(f"Agent {self.agent_id}: Error parsing params for '{xml_block_to_parse[:100]}...': {parse_err}", exc_info=True); return None
        # End Helper Function

        # --- Corrected Indentation for Parsing Loops ---
        markdown_matches = []
        if self.markdown_xml_tool_call_pattern:
            for m in self.markdown_xml_tool_call_pattern.finditer(buffer_content):
                # This block is now correctly indented under the 'for m' loop
                parsed = parse_single_match(m, True)
                if parsed:
                    markdown_matches.append(parsed) # Correct indentation

        raw_matches = []
        if self.raw_xml_tool_call_pattern:
             for m in self.raw_xml_tool_call_pattern.finditer(buffer_content):
                 # This block is now correctly indented under the 'for m' loop
                 parsed = parse_single_match(m, False)
                 if parsed:
                      raw_matches.append(parsed) # Correct indentation
        # --- End Correction ---

        found_calls = markdown_matches + raw_matches; found_calls.sort(key=lambda x: x[2][0])
        if not found_calls: logger.debug(f"Agent {self.agent_id}: No valid tool calls found.")
        else: logger.info(f"Agent {self.agent_id}: Found {len(found_calls)} valid tool call(s).")
        return found_calls
    # --- *** END METHOD *** ---


    async def process_message(self) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """ Processes the task. Yields events ('response_chunk', 'tool_requests', 'final_response', 'error'). """
        if self.status not in [AGENT_STATUS_IDLE]:
            logger.warning(f"Agent {self.agent_id} process_message called but not idle (Status: {self.status})."); yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}; return
        if not self.llm_provider: logger.error(f"Agent {self.agent_id}: LLM Provider not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}; return
        if not self.manager: logger.error(f"Agent {self.agent_id}: Manager not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: Manager not configured]"}; return

        self.set_status(AGENT_STATUS_PROCESSING); self.text_buffer = ""; complete_assistant_response = ""; stream_had_error = False
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(self.message_history)}")
        try:
            if not self.ensure_sandbox_exists(): self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]"}; return
            provider_stream = self.llm_provider.stream_completion(messages=self.message_history, model=self.model, temperature=self.temperature, **self.provider_kwargs)
            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk": content = event.get("content", "");
                    if content: self.text_buffer += content; complete_assistant_response += content; yield {"type": "response_chunk", "content": content}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error": error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"; logger.error(f"Agent {self.agent_id}: Received error event: {error_content}"); event["agent_id"] = self.agent_id; event["content"] = error_content; stream_had_error = True; yield event
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}'.")
            logger.debug(f"Agent {self.agent_id}: Stream finished. Error: {stream_had_error}. Processing buffer.")
            if not stream_had_error:
                parsed_tool_calls = self._find_and_parse_tool_calls()
                if parsed_tool_calls:
                     logger.info(f"Agent {self.agent_id}: {len(parsed_tool_calls)} tool call(s) found.")
                     tool_requests_list = []
                     for tool_name, tool_args, (match_start, match_end) in parsed_tool_calls:
                         call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"; tool_requests_list.append({"id": call_id, "name": tool_name, "arguments": tool_args}); await asyncio.sleep(0.001)
                     self.set_status(AGENT_STATUS_AWAITING_TOOL); logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} XML tool request(s)."); yield {"type": "tool_requests", "calls": tool_requests_list, "raw_assistant_response": complete_assistant_response}
                else: logger.debug(f"Agent {self.agent_id}: No tool calls found.");
                     if complete_assistant_response: logger.debug(f"Agent {self.agent_id}: Yielding final_response."); yield {"type": "final_response", "content": complete_assistant_response}
            else: logger.warning(f"Agent {self.agent_id}: Skipping final processing due to stream error.")
        except Exception as e: error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"; logger.error(error_msg, exc_info=True); yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            if self.status == AGENT_STATUS_PROCESSING and not stream_had_error: self.set_status(AGENT_STATUS_IDLE)
            self.text_buffer = ""; logger.info(f"Agent {self.agent_id}: Finished cycle attempt. Final Status was: {self.status}")

    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        state = {
            "agent_id": self.agent_id, "persona": self.persona, "status": self.status,
            "provider": self.provider_name, "model": self.model, "temperature": self.temperature,
            "message_history_length": len(self.message_history), "sandbox_path": str(self.sandbox_path),
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state["current_tool"] = self.current_tool_info
        return state

    def clear_history(self):
        """Clears message history, keeps system prompt."""
        logger.info(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = [{"role": "system", "content": self.final_system_prompt}]
