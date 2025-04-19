# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
import html # For unescaping XML parameter values
import logging # Added logging

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict # Removed ToolDict

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Constants (Unchanged)
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# Tool Call Patterns (Reverted to XML only)
XML_TOOL_CALL_PATTERN = None # Compiled in __init__
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```" # Compiled in __init__

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    parsing XML tool calls from responses (handling potential markdown fences),
    and yielding requests. Tracks its own status.
    Relies on the system_prompt within its config for all instructions, including tools.
    Now relies on AgentManager/CycleHandler for error handling beyond basic provider issues.
    """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider, # Inject the provider instance
        manager: 'AgentManager' # Manager is now required
        ):
        """
        Initializes an Agent instance using configuration and injected dependencies.
        The final system prompt, including tool descriptions, should be present
        within the agent_config dictionary.

        Args:
            agent_config (Dict[str, Any]): Configuration dictionary for this agent.
                                          MUST contain ['config']['system_prompt'].
            llm_provider (BaseLLMProvider): An initialized instance of an LLM provider.
            manager ('AgentManager'): A reference to the agent manager (required).
        """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL) # This will be set correctly later by lifecycle
        self.final_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        if not self.final_system_prompt: logger.error(f"Agent {self.agent_id}: 'system_prompt' is missing or empty!"); self.final_system_prompt = "You are a helpful assistant."
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.agent_config: Dict[str, Any] = agent_config
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        logger.debug(f"Agent {self.agent_id} using final system prompt (first 500 chars):\n{self.final_system_prompt[:500]}...")
        self.status: str = AGENT_STATUS_IDLE
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.message_history: List[MessageDict] = []
        self.message_history.append({"role": "system", "content": self.final_system_prompt})
        self._last_api_key_used: Optional[str] = None
        self._failed_models_this_cycle: set = set()
        self.text_buffer: str = ""
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        # Compile the XML regex patterns for tool detection
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                safe_tool_names_lower = [re.escape(name.lower()) for name in tool_names]
                tool_names_pattern_group_lower = '|'.join(safe_tool_names_lower)
                raw_pattern_str = rf"<({tool_names_pattern_group_lower})>([\s\S]*?)</\1>"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)
                md_xml_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group_lower)
                self.markdown_xml_tool_call_pattern = re.compile(md_xml_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)
                logger.info(f"Agent {self.agent_id}: Compiled XML tool patterns for tools: {tool_names}")
                logger.debug(f"Agent {self.agent_id}: Raw XML pattern: {self.raw_xml_tool_call_pattern.pattern}")
            else: logger.info(f"Agent {self.agent_id}: No tools found in executor, tool parsing disabled.")
        else: logger.warning(f"Agent {self.agent_id}: Manager or ToolExecutor not available during init, tool parsing disabled.")
        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management (Unchanged) ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        if self.status != new_status: logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else: logger.warning(f"Agent {self.agent_id}: Manager not set, cannot push status update.")

    # --- Dependency Setters (Unchanged) ---
    def set_manager(self, manager: 'AgentManager'): self.manager = manager
    def set_tool_executor(self, tool_executor: Any): logger.warning(f"Agent {self.agent_id}: set_tool_executor called but ToolExecutor is no longer directly used by Agent.")

    # --- Sandbox Creation (Unchanged) ---
    def ensure_sandbox_exists(self) -> bool:
        try: self.sandbox_path.mkdir(parents=True, exist_ok=True); return True
        except OSError as e: logger.error(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}"); return False
        except Exception as e: logger.error(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}", exc_info=True); return False

    # --- Tool Call Parsing Helper (ADDED MORE DEBUGGING) ---
    def _find_and_parse_tool_calls(self) -> List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
        """
        Finds *all* occurrences of valid tool calls (raw or fenced) in the text_buffer,
        avoiding nested matches within fenced blocks. Parses them and returns validated info.
        """
        if not self.text_buffer: return []
        buffer_content = self.text_buffer.strip()
        logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] Checking stripped buffer for XML tool calls (Len: {len(buffer_content)}):\n>>>\n{buffer_content}\n<<<")
        found_calls = []; processed_spans = set()

        def is_overlapping(start, end):
            for proc_start, proc_end in processed_spans:
                if max(start, proc_start) < min(end, proc_end): return True
            return False

        def parse_single_match(match, is_markdown):
             match_start, match_end = match.span()
             for proc_start, proc_end in processed_spans:
                  if max(match_start, proc_start) < min(match_end, proc_end): logger.debug(f"[PARSE_DEBUG] Skipping overlapping match at ({match_start}, {match_end})"); return None
             xml_block_to_parse = ""; tool_name_from_outer_match = ""
             if is_markdown: xml_block_to_parse = match.group(1).strip(); tool_name_from_outer_match = match.group(2)
             else: xml_block_to_parse = match.group(0).strip(); tool_name_from_outer_match = match.group(1)
             try:
                 if not self.raw_xml_tool_call_pattern: logger.error("[PARSE_DEBUG] Raw XML tool call pattern not compiled!"); return None
                 tool_name = next((name for name in self.manager.tool_executor.tools if name.lower() == tool_name_from_outer_match.lower()), None)
                 if not tool_name: logger.warning(f"[PARSE_DEBUG] Agent {self.agent_id}: Found <{tool_name_from_outer_match}> but no matching tool registered."); return None
                 inner_match = self.raw_xml_tool_call_pattern.search(xml_block_to_parse)
                 if not inner_match: logger.warning(f"[PARSE_DEBUG] Agent {self.agent_id}: Could not parse inner content: '{xml_block_to_parse}'"); return None
                 inner_tool_name, inner_content = inner_match.groups()
                 if inner_tool_name.lower() != tool_name_from_outer_match.lower(): logger.warning(f"[PARSE_DEBUG] Agent {self.agent_id}: Tool name mismatch ({tool_name} vs {inner_tool_name})."); return None
                 logger.info(f"Agent {self.agent_id}: Detected call for tool '{tool_name}' at span ({match_start}, {match_end}) (MD: {is_markdown})")
                 tool_args = {}; param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"; param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                 for param_name, param_value_escaped in param_matches: tool_args[param_name] = html.unescape(param_value_escaped.strip()) # Store original case for validation
                 logger.info(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}")
                 # Parameter Validation (using original case from schema)
                 validated_args = {}; valid_structure = True; tool_schema = self.manager.tool_executor.tools[tool_name].get_schema(); expected_params_dict = {p['name']: p for p in tool_schema.get('parameters', [])}
                 for param_name_key, param_value in tool_args.items():
                      # Case-insensitive check against schema keys
                      expected_param_info = None
                      for schema_name, schema_info in expected_params_dict.items():
                           if schema_name.lower() == param_name_key.lower():
                                expected_param_info = schema_info
                                break
                      if not expected_param_info: logger.warning(f"Agent {self.agent_id}: Unknown parameter '{param_name_key}' provided for tool '{tool_name}'. Ignoring."); continue
                      expected_type = expected_param_info['type']; original_param_name = expected_param_info['name'] # Use original schema name case
                      try:
                          if expected_type == 'integer': validated_args[original_param_name] = int(param_value)
                          elif expected_type == 'float': validated_args[original_param_name] = float(param_value)
                          elif expected_type == 'boolean': validated_args[original_param_name] = str(param_value).lower() in ['true', '1', 'yes']
                          else: validated_args[original_param_name] = str(param_value)
                      except ValueError: logger.warning(f"Agent {self.agent_id}: Invalid value type for parameter '{original_param_name}' (expected {expected_type}) in tool '{tool_name}'. Value: '{param_value}'"); valid_structure = False; break
                 if not valid_structure: return None
                 missing_required = [];
                 for p_name, p_info in expected_params_dict.items():
                      if p_info.get('required', True) and p_name not in validated_args: missing_required.append(p_name)
                 if missing_required:
                      is_create_agent_optional_missing = (tool_name == "ManageTeamTool" and tool_args.get("action") == "create_agent" and all(p in ['provider', 'model'] for p in missing_required))
                      if not is_create_agent_optional_missing: logger.warning(f"Agent {self.agent_id}: Missing required parameters for tool '{tool_name}': {missing_required}"); return None
                      else: logger.debug(f"Allowing ManageTeamTool create_agent without provider/model (will be auto-selected).")

                 processed_spans.add((match_start, match_end)); return tool_name, validated_args, (match_start, match_end)
             except Exception as parse_err: logger.error(f"Agent {self.agent_id}: Error parsing params for '{xml_block_to_parse[:100]}...': {parse_err}", exc_info=True); return None
        # End Helper Function

        markdown_matches = []
        if self.markdown_xml_tool_call_pattern:
            logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] Searching for MARKDOWN XML...")
            try:
                 markdown_matches_found = list(self.markdown_xml_tool_call_pattern.finditer(buffer_content))
                 logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] Found {len(markdown_matches_found)} potential markdown XML matches.")
                 for m in markdown_matches_found:
                     parsed = parse_single_match(m, True)
                     if parsed: markdown_matches.append(parsed)
            except Exception as e:
                 logger.error(f"Agent {self.agent_id}: [PARSE_DEBUG] Error during markdown XML finditer: {e}", exc_info=True)

        raw_matches = []
        if self.raw_xml_tool_call_pattern:
             logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] Searching for RAW XML using pattern: {self.raw_xml_tool_call_pattern.pattern} ...")
             raw_matches_found = [] # Init empty list
             try:
                 # --- ADDED EXCEPTION HANDLING & IMMEDIATE LIST CONVERSION ---
                 raw_matches_found = list(self.raw_xml_tool_call_pattern.finditer(buffer_content))
                 # --- END ADD ---
                 logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] Found {len(raw_matches_found)} potential raw XML matches.")
                 for m in raw_matches_found: # Iterate over the materialized list
                     parsed = parse_single_match(m, False)
                     if parsed: raw_matches.append(parsed)
             except Exception as e:
                 logger.error(f"Agent {self.agent_id}: [PARSE_DEBUG] Error during raw XML finditer: {e}", exc_info=True)


        found_calls = markdown_matches + raw_matches; found_calls.sort(key=lambda x: x[2][0])
        if not found_calls: logger.debug(f"Agent {self.agent_id}: [PARSE_DEBUG] No valid XML tool calls found in buffer.")
        else: logger.info(f"Agent {self.agent_id}: Found {len(found_calls)} valid XML tool call(s) in buffer.")
        return found_calls


    # --- Main Processing Logic (Unchanged) ---
    async def process_message(self) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        if self.status not in [AGENT_STATUS_IDLE]: logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle (Status: {self.status})."); yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}; return
        if not self.llm_provider: logger.error(f"Agent {self.agent_id}: LLM Provider not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}; return
        if not self.manager: logger.error(f"Agent {self.agent_id}: Manager not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: Manager not configured]"}; return
        self.set_status(AGENT_STATUS_PROCESSING); self.text_buffer = ""; complete_assistant_response = ""; stream_had_error = False; last_error_obj = None
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(self.message_history)}")
        try:
            if not self.ensure_sandbox_exists(): self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]"}; return
            provider_stream = self.llm_provider.stream_completion( messages=self.message_history, model=self.model, temperature=self.temperature, **self.provider_kwargs )
            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk": content = event.get("content", "");
                if content: self.text_buffer += content; complete_assistant_response += content; yield {"type": "response_chunk", "content": content}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    last_error_obj = event.get("_exception_obj", ValueError(error_content))
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished processing. Stream had error: {stream_had_error}. Processing final buffer.")
            if not stream_had_error:
                parsed_tool_calls = self._find_and_parse_tool_calls()
                if parsed_tool_calls:
                     logger.info(f"Agent {self.agent_id}: {len(parsed_tool_calls)} tool call(s) found in final buffer.")
                     tool_requests_list = []
                     for tool_name, tool_args, (match_start, match_end) in parsed_tool_calls:
                         call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                         tool_requests_list.append({"id": call_id, "name": tool_name, "arguments": tool_args})
                         await asyncio.sleep(0.001)
                     self.set_status(AGENT_STATUS_AWAITING_TOOL)
                     logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} XML tool request(s).")
                     yield {"type": "tool_requests", "calls": tool_requests_list, "raw_assistant_response": complete_assistant_response}
                else:
                     logger.debug(f"Agent {self.agent_id}: No tool calls found in final buffer.")
                     if complete_assistant_response: logger.debug(f"Agent {self.agent_id}: Yielding final_response event (no tool calls)."); yield {"type": "final_response", "content": complete_assistant_response}
            else: logger.warning(f"Agent {self.agent_id}: Skipping final tool parsing/response yielding because stream yielded an error.")
        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]", "_exception_obj": e}
        finally:
            self.text_buffer = ""; logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Status before cycle handler finalizes: {self.status}")

    # --- get_state and clear_history (Unchanged) ---
    def get_state(self) -> Dict[str, Any]:
        state = { "agent_id": self.agent_id, "persona": self.persona, "status": self.status, "provider": self.provider_name, "model": self.model, "temperature": self.temperature, "message_history_length": len(self.message_history), "sandbox_path": str(self.sandbox_path), "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None) }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state["current_tool"] = self.current_tool_info
        return state
    def clear_history(self):
        logger.info(f"Clearing message history for Agent {self.agent_id}"); self.message_history = [{"role": "system", "content": self.final_system_prompt}]
