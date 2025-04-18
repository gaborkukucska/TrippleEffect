# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
# import xml.etree.ElementTree as ET # Keep commented unless needed for complex parsing
import html # For unescaping
import logging # Added logging

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict # Removed ToolDict

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # No longer need ToolExecutor here
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Constants (Removed AWAITING_USER_OVERRIDE)
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# Tool Call Patterns (compiled in __init__)
XML_TOOL_CALL_PATTERN = None # For raw XML: <tool>...</tool>
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```" # For XML in ```
MARKDOWN_FENCE_JSON_PATTERN = r"```json\s*(\{[\s\S]*?\})\s*```" # For JSON in ```json ... ```


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

        # Core configuration from agent_config, falling back to global defaults
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        # Use the system prompt provided in the config - it should include standard instructions now
        self.final_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        if not self.final_system_prompt:
             logger.error(f"Agent {self.agent_id}: 'system_prompt' is missing or empty in agent_config['config']!")
             # Provide a minimal default to avoid errors, but this is likely a config issue
             self.final_system_prompt = "You are a helpful assistant."

        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        # Store the full config entry used to create this agent (useful for saving/loading/override)
        self.agent_config: Dict[str, Any] = agent_config # Store the full entry

        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}

        # Injected dependencies
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager # Manager is essential now

        logger.debug(f"Agent {self.agent_id} using final system prompt (first 500 chars):\n{self.final_system_prompt[:500]}...")

        # State management
        self.status: str = AGENT_STATUS_IDLE
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.message_history: List[MessageDict] = [] # Initialize empty, add prompt below
        self.message_history.append({"role": "system", "content": self.final_system_prompt})

        # Buffers for processing stream
        self.text_buffer: str = "" # Accumulates text chunks from LLM

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        # Compile the regex patterns for tool detection
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        self.markdown_json_tool_call_pattern = None # Add pattern for JSON
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                 # Ensure tool names are regex-safe
                safe_tool_names = [re.escape(name) for name in tool_names]
                tool_names_pattern_group = '|'.join(safe_tool_names)

                # Pattern for raw XML block: captures tool name in group 1, inner content in group 2
                raw_pattern_str = rf"<({tool_names_pattern_group})>([\s\S]*?)</\1>"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)

                # Pattern for XML within markdown fence: captures full XML block in group 1, tool name in group 2
                md_xml_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group)
                self.markdown_xml_tool_call_pattern = re.compile(md_xml_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)

                # Pattern for JSON within markdown fence: captures JSON object in group 1
                self.markdown_json_tool_call_pattern = re.compile(MARKDOWN_FENCE_JSON_PATTERN, re.DOTALL | re.MULTILINE)

                logger.info(f"Agent {self.agent_id}: Compiled XML and JSON tool patterns for tools: {tool_names}")
            else:
                logger.info(f"Agent {self.agent_id}: No tools found in executor, tool parsing disabled.")
        else:
            logger.warning(f"Agent {self.agent_id}: Manager or ToolExecutor not available during init, tool parsing disabled.")


        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        """Updates the agent's status and optionally tool info."""
        if self.status != new_status: # Only log if status changes
             logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        if self.manager:
            # Use asyncio.create_task to avoid blocking agent if manager takes time
            asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else:
             logger.warning(f"Agent {self.agent_id}: Manager not set, cannot push status update.")


    # --- Dependency Setters ---
    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def set_tool_executor(self, tool_executor: Any): # Added type Any to match old signature
         logger.warning(f"Agent {self.agent_id}: set_tool_executor called but ToolExecutor is no longer directly used by Agent.")
         pass

    def ensure_sandbox_exists(self) -> bool:
        """Creates the agent's sandbox directory if it doesn't exist."""
        try:
            self.sandbox_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.error(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}", exc_info=True)
            return False

    # --- Tool Call Parsing Helper (Handles XML and JSON) ---
    def _find_and_parse_tool_calls(self) -> List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
        """
        Finds *all* occurrences of valid tool calls (XML raw, XML fenced, JSON fenced)
        in the text_buffer, avoiding nested matches. Parses them and returns validated info.
        """
        if not self.text_buffer: return []
        buffer_content = self.text_buffer
        logger.debug(f"Agent {self.agent_id}: Checking buffer for tool calls (Len: {len(buffer_content)}): '{buffer_content[-500:]}'")
        found_calls = []; processed_spans = set()

        # --- Helper to check for overlaps ---
        def is_overlapping(start, end):
            for proc_start, proc_end in processed_spans:
                if max(start, proc_start) < min(end, proc_end):
                    return True
            return False

        # --- 1. Find Markdown JSON ```json ... ``` blocks ---
        json_matches = []
        if self.markdown_json_tool_call_pattern:
            for match in self.markdown_json_tool_call_pattern.finditer(buffer_content):
                match_start, match_end = match.span()
                if is_overlapping(match_start, match_end): continue

                json_string = match.group(1).strip()
                try:
                    tool_call_data = json.loads(json_string)
                    tool_name = tool_call_data.get("tool_name")
                    tool_args = tool_call_data.get("parameters", {})

                    if not isinstance(tool_name, str) or not isinstance(tool_args, dict):
                        logger.warning(f"Agent {self.agent_id}: Invalid JSON structure in tool call block: {json_string[:100]}...")
                        continue

                    # Validate tool name exists
                    if tool_name not in self.manager.tool_executor.tools:
                        logger.warning(f"Agent {self.agent_id}: Found JSON tool call for unknown tool '{tool_name}'.")
                        continue

                    # --- Added: Validate parameter types ---
                    validated_args = {}
                    valid_structure = True
                    tool_schema = self.manager.tool_executor.tools[tool_name].get_schema()
                    expected_params = {p['name']: p for p in tool_schema.get('parameters', [])}

                    for param_name, param_value in tool_args.items():
                        if param_name not in expected_params:
                            logger.warning(f"Agent {self.agent_id}: Unknown parameter '{param_name}' provided for tool '{tool_name}'. Ignoring.")
                            continue
                        # Basic type check/conversion (can be expanded)
                        expected_type = expected_params[param_name]['type']
                        try:
                            if expected_type == 'integer': validated_args[param_name] = int(param_value)
                            elif expected_type == 'float': validated_args[param_name] = float(param_value)
                            elif expected_type == 'boolean': validated_args[param_name] = str(param_value).lower() in ['true', '1', 'yes']
                            else: validated_args[param_name] = str(param_value) # Default to string
                        except ValueError:
                             logger.warning(f"Agent {self.agent_id}: Invalid value type for parameter '{param_name}' (expected {expected_type}) in tool '{tool_name}'. Value: '{param_value}'")
                             valid_structure = False; break # Stop processing this tool call if type is wrong

                    if not valid_structure: continue # Skip this tool call if validation failed

                    # Check for missing required parameters
                    missing_required = []
                    for p_name, p_info in expected_params.items():
                         if p_info.get('required', True) and p_name not in validated_args:
                              missing_required.append(p_name)
                    if missing_required:
                         logger.warning(f"Agent {self.agent_id}: Missing required parameters for tool '{tool_name}': {missing_required}")
                         continue # Skip this tool call

                    logger.info(f"Agent {self.agent_id}: Detected JSON tool call for '{tool_name}' at span ({match_start}, {match_end}). Validated Args: {validated_args}")
                    json_matches.append((tool_name, validated_args, (match_start, match_end))) # Use validated_args
                    processed_spans.add((match_start, match_end)) # Mark span as processed

                except json.JSONDecodeError as e:
                    logger.warning(f"Agent {self.agent_id}: Failed to decode JSON tool call block: {e}. Content: {json_string[:100]}...")
                except Exception as e:
                    logger.error(f"Agent {self.agent_id}: Unexpected error parsing JSON tool call: {e}", exc_info=True)

        # --- 2. Find Markdown XML ```<tool>...</tool>``` blocks ---
        markdown_xml_matches = []
        if self.markdown_xml_tool_call_pattern:
            for match in self.markdown_xml_tool_call_pattern.finditer(buffer_content):
                match_start, match_end = match.span()
                if is_overlapping(match_start, match_end): continue

                xml_block = match.group(1).strip()
                tool_name_outer = match.group(2)
                tool_name = next((name for name in self.manager.tool_executor.tools if name.lower() == tool_name_outer.lower()), None)

                if not tool_name:
                    logger.warning(f"Agent {self.agent_id}: Found fenced XML <{tool_name_outer}> but no matching tool registered.")
                    continue

                try:
                    # Re-use raw pattern to extract inner content easily
                    inner_match = self.raw_xml_tool_call_pattern.search(xml_block) if self.raw_xml_tool_call_pattern else None
                    if not inner_match or inner_match.group(1).lower() != tool_name.lower():
                        logger.warning(f"Agent {self.agent_id}: Could not parse inner content of fenced XML: '{xml_block[:100]}...'")
                        continue

                    inner_content = inner_match.group(2)
                    tool_args = {}; param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"; param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                    for param_name, param_value_escaped in param_matches: tool_args[param_name] = html.unescape(param_value_escaped.strip())

                    # --- Added: Validate parameter types for XML ---
                    validated_args = {}
                    valid_structure = True
                    tool_schema = self.manager.tool_executor.tools[tool_name].get_schema()
                    expected_params = {p['name']: p for p in tool_schema.get('parameters', [])}

                    for param_name, param_value in tool_args.items():
                         if param_name not in expected_params:
                             logger.warning(f"Agent {self.agent_id}: Unknown parameter '{param_name}' provided for XML tool '{tool_name}'. Ignoring.")
                             continue
                         expected_type = expected_params[param_name]['type']
                         try:
                             if expected_type == 'integer': validated_args[param_name] = int(param_value)
                             elif expected_type == 'float': validated_args[param_name] = float(param_value)
                             elif expected_type == 'boolean': validated_args[param_name] = str(param_value).lower() in ['true', '1', 'yes']
                             else: validated_args[param_name] = str(param_value)
                         except ValueError:
                              logger.warning(f"Agent {self.agent_id}: Invalid value type for XML parameter '{param_name}' (expected {expected_type}) in tool '{tool_name}'. Value: '{param_value}'")
                              valid_structure = False; break

                    if not valid_structure: continue

                    missing_required = []
                    for p_name, p_info in expected_params.items():
                         if p_info.get('required', True) and p_name not in validated_args:
                              missing_required.append(p_name)
                    if missing_required:
                         logger.warning(f"Agent {self.agent_id}: Missing required XML parameters for tool '{tool_name}': {missing_required}")
                         continue

                    logger.info(f"Agent {self.agent_id}: Detected fenced XML tool call for '{tool_name}' at span ({match_start}, {match_end}). Validated Args: {validated_args}")
                    markdown_xml_matches.append((tool_name, validated_args, (match_start, match_end))) # Use validated_args
                    processed_spans.add((match_start, match_end))

                except Exception as parse_err:
                    logger.error(f"Agent {self.agent_id}: Error parsing params for fenced XML '{xml_block[:100]}...': {parse_err}", exc_info=True)

        # --- 3. Find Raw XML <tool>...</tool> blocks ---
        raw_xml_matches = []
        if self.raw_xml_tool_call_pattern:
             for match in self.raw_xml_tool_call_pattern.finditer(buffer_content):
                 match_start, match_end = match.span()
                 if is_overlapping(match_start, match_end): continue

                 tool_name_outer = match.group(1)
                 tool_name = next((name for name in self.manager.tool_executor.tools if name.lower() == tool_name_outer.lower()), None)

                 if not tool_name:
                     # This might be common if the LLM hallucinates tags, keep log level lower
                     logger.debug(f"Agent {self.agent_id}: Found raw XML <{tool_name_outer}> but no matching tool registered.")
                     continue

                 try:
                     inner_content = match.group(2)
                     tool_args = {}; param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"; param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                     for param_name, param_value_escaped in param_matches: tool_args[param_name] = html.unescape(param_value_escaped.strip())

                     # --- Added: Validate parameter types for Raw XML ---
                     validated_args = {}
                     valid_structure = True
                     tool_schema = self.manager.tool_executor.tools[tool_name].get_schema()
                     expected_params = {p['name']: p for p in tool_schema.get('parameters', [])}

                     for param_name, param_value in tool_args.items():
                          if param_name not in expected_params:
                              logger.warning(f"Agent {self.agent_id}: Unknown parameter '{param_name}' provided for raw XML tool '{tool_name}'. Ignoring.")
                              continue
                          expected_type = expected_params[param_name]['type']
                          try:
                              if expected_type == 'integer': validated_args[param_name] = int(param_value)
                              elif expected_type == 'float': validated_args[param_name] = float(param_value)
                              elif expected_type == 'boolean': validated_args[param_name] = str(param_value).lower() in ['true', '1', 'yes']
                              else: validated_args[param_name] = str(param_value)
                          except ValueError:
                               logger.warning(f"Agent {self.agent_id}: Invalid value type for raw XML parameter '{param_name}' (expected {expected_type}) in tool '{tool_name}'. Value: '{param_value}'")
                               valid_structure = False; break

                     if not valid_structure: continue

                     missing_required = []
                     for p_name, p_info in expected_params.items():
                          if p_info.get('required', True) and p_name not in validated_args:
                               missing_required.append(p_name)
                     if missing_required:
                          logger.warning(f"Agent {self.agent_id}: Missing required raw XML parameters for tool '{tool_name}': {missing_required}")
                          continue

                     logger.info(f"Agent {self.agent_id}: Detected raw XML tool call for '{tool_name}' at span ({match_start}, {match_end}). Validated Args: {validated_args}")
                     raw_xml_matches.append((tool_name, validated_args, (match_start, match_end))) # Use validated_args
                     processed_spans.add((match_start, match_end))

                 except Exception as parse_err:
                     logger.error(f"Agent {self.agent_id}: Error parsing params for raw XML '{match.group(0)[:100]}...': {parse_err}", exc_info=True)

        # Combine all found calls and sort by starting position
        found_calls = json_matches + markdown_xml_matches + raw_xml_matches
        found_calls.sort(key=lambda x: x[2][0])

        if not found_calls: logger.debug(f"Agent {self.agent_id}: No valid tool calls found.")
        else: logger.info(f"Agent {self.agent_id}: Found {len(found_calls)} valid tool call(s) across all formats.")
        return found_calls


    # --- Main Processing Logic (Restored) ---
    async def process_message(self) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes the task based on the current message history using the LLM provider.
        Parses the response stream for XML tool calls and yields requests.
        Relies on CycleHandler for retry/failover logic.

        Yields:
            Dict[str, Any]: Events ('response_chunk', 'tool_requests', 'final_response', 'error', 'status').
        Receives:
             Optional[List[ToolResultDict]]: Currently ignored. CycleHandler manages loop continuation.
        """
        if self.status not in [AGENT_STATUS_IDLE]:
            logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle (Status: {self.status}).")
            yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}
            return

        if not self.llm_provider: logger.error(f"Agent {self.agent_id}: LLM Provider not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}; return
        if not self.manager: logger.error(f"Agent {self.agent_id}: Manager not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: Manager not configured]"}; return

        self.set_status(AGENT_STATUS_PROCESSING)
        self.text_buffer = "" # Clear buffer at the start of processing
        complete_assistant_response = "" # Accumulate the full response for history/final event
        stream_had_error = False # Flag if an error event was yielded by the provider stream

        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(self.message_history)}")

        try:
            if not self.ensure_sandbox_exists(): self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]"}; return

            provider_stream = self.llm_provider.stream_completion(
                messages=self.message_history, # Use current history
                model=self.model,
                temperature=self.temperature,
                # No tools/tool_choice passed here, handled by XML parsing
                **self.provider_kwargs
            )

            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk":
                    content = event.get("content", "");
                    if content: self.text_buffer += content; complete_assistant_response += content; yield {"type": "response_chunk", "content": content}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; stream_had_error = True; yield event
                    # Don't break here, let CycleHandler manage retry/failover based on error
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            logger.debug(f"Agent {self.agent_id}: Provider stream finished processing. Stream had error: {stream_had_error}. Processing final buffer.")

            if not stream_had_error:
                parsed_tool_calls = self._find_and_parse_tool_calls()
                if parsed_tool_calls:
                     logger.info(f"Agent {self.agent_id}: {len(parsed_tool_calls)} tool call(s) found in final buffer.")
                     tool_requests_list = []
                     for tool_name, tool_args, (match_start, match_end) in parsed_tool_calls:
                         # Use a more descriptive prefix based on format?
                         call_id = f"tool_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                         tool_requests_list.append({"id": call_id, "name": tool_name, "arguments": tool_args})
                         await asyncio.sleep(0.001) # Ensure unique ID
                     self.set_status(AGENT_STATUS_AWAITING_TOOL)
                     logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} tool request(s).")
                     # Send the raw assistant response along with tool calls for history
                     yield {"type": "tool_requests", "calls": tool_requests_list, "raw_assistant_response": complete_assistant_response}
                else: # No tool calls found
                     logger.debug(f"Agent {self.agent_id}: No tool calls found in final buffer.")
                     if complete_assistant_response: # Only yield final if there's content
                          logger.debug(f"Agent {self.agent_id}: Yielding final_response event (no tool calls).")
                          yield {"type": "final_response", "content": complete_assistant_response}
            else: logger.warning(f"Agent {self.agent_id}: Skipping final tool parsing/response yielding because stream yielded an error.")

        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            # Yield error for CycleHandler to process (triggers failover)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            # CycleHandler now determines the final state (IDLE or ERROR)
            # Only reset buffer here.
            self.text_buffer = ""
            logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Status before cycle handler finalizes: {self.status}")


    # --- get_state and clear_history (Restored) ---
    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        state = {
            "agent_id": self.agent_id, "persona": self.persona, "status": self.status,
            "provider": self.provider_name, "model": self.model, "temperature": self.temperature,
            "message_history_length": len(self.message_history), "sandbox_path": str(self.sandbox_path),
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info
        return state

    def clear_history(self):
        """Clears message history, keeps system prompt."""
        logger.info(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = [{"role": "system", "content": self.final_system_prompt}]
