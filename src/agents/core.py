# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
# import xml.etree.ElementTree as ET # Keep ET commented unless needed for complex parsing
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

# Constants
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"
AGENT_STATUS_AWAITING_USER_OVERRIDE = "awaiting_user_override"

# --- Regex for XML Tool Call Detection ---
# We'll compile this in __init__ based on available tools.
XML_TOOL_CALL_PATTERN = None # Will be set in __init__

# --- Regex to potentially find XML within markdown code fences ---
# Looks for ```[optional language specifier]\n<tool_name>...</tool_name>\n```
# It's non-greedy and captures the *full XML block* inside (group 1) and the *tool name* itself (group 2).
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```"


class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    parsing XML tool calls from responses (handling potential markdown fences),
    and yielding requests. Tracks its own status.
    """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider, # Inject the provider instance
        manager: 'AgentManager', # Manager is now required
        tool_descriptions_xml: str # Inject the formatted tool descriptions
        ):
        """
        Initializes an Agent instance using configuration and injected dependencies.

        Args:
            agent_config (Dict[str, Any]): Configuration dictionary for this agent.
            llm_provider (BaseLLMProvider): An initialized instance of an LLM provider.
            manager ('AgentManager'): A reference to the agent manager (required).
            tool_descriptions_xml (str): Formatted XML string describing available tools.
        """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")

        # Core configuration from agent_config, falling back to global defaults
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        # Store the original system prompt separately if needed for resets, though combined is used mostly
        self.original_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        # Store the full config entry used to create this agent (useful for saving/loading/override)
        self.agent_config: Dict[str, Any] = agent_config # Store the full entry

        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}

        # Injected dependencies
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager # Manager is essential now

        # Combine original prompt with dynamic tool descriptions
        # Use the prompt from the stored agent_config as it might have been updated (e.g., team ID)
        current_system_prompt = self.agent_config.get("config", {}).get("system_prompt", self.original_system_prompt)
        self.final_system_prompt: str = current_system_prompt # Initial value before tools might be added
        # Check if the tool descriptions XML marker is already present
        tool_desc_marker = "# Tools Description (XML Format)"
        if tool_desc_marker not in self.final_system_prompt:
            self.final_system_prompt += "\n\n" + tool_descriptions_xml
            # Also update the stored config if we modified the prompt
            if "config" in self.agent_config:
                 self.agent_config["config"]["system_prompt"] = self.final_system_prompt

        logger.debug(f"Agent {self.agent_id} Final System Prompt (first 500 chars):\n{self.final_system_prompt[:500]}...")

        # State management
        self.status: str = AGENT_STATUS_IDLE
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.message_history: List[MessageDict] = [] # Initialize empty, add prompt below
        self.message_history.append({"role": "system", "content": self.final_system_prompt})

        # Buffers for processing stream
        self.text_buffer: str = "" # Accumulates text chunks from LLM

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        # Compile the regex pattern for tool detection using known tool names from the manager's executor
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                 # Ensure tool names are regex-safe (though they should be simple)
                safe_tool_names = [re.escape(name) for name in tool_names]
                tool_names_pattern_group = '|'.join(safe_tool_names)

                # Pattern for raw XML block: captures tool name in group 1, inner content in group 2
                raw_pattern_str = rf"<({tool_names_pattern_group})>([\s\S]*?)</\1>"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)

                # Pattern for XML within markdown fence: captures full XML block in group 1, tool name in group 2
                md_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group)
                self.markdown_xml_tool_call_pattern = re.compile(md_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)

                logger.info(f"Agent {self.agent_id}: Compiled XML tool patterns for tools: {tool_names}")
                logger.debug(f"  Raw Pattern: {self.raw_xml_tool_call_pattern.pattern}")
                logger.debug(f"  Markdown Pattern: {self.markdown_xml_tool_call_pattern.pattern}")
            else:
                logger.info(f"Agent {self.agent_id}: No tools found in executor, XML parsing disabled.")
        else:
            logger.warning(f"Agent {self.agent_id}: Manager or ToolExecutor not available during init, XML parsing disabled.")


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

    # --- *** MODIFIED XML Parsing Helper to find ALL calls *** ---
    def _find_and_parse_tool_calls(self) -> List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
        """
        Finds *all* occurrences of valid tool calls (raw or fenced) in the text_buffer,
        avoiding nested matches within fenced blocks. Parses them and returns validated info.

        Returns:
            List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
                A list of tuples: (tool_name, tool_args, (match_start_index, match_end_index))
                Returns an empty list if no valid calls are found.
        """
        if not self.text_buffer:
            return []

        buffer_content = self.text_buffer
        logger.debug(f"Agent {self.agent_id}: Checking buffer for tool calls (Length: {len(buffer_content)}): '{buffer_content[-500:]}'") # Log more chars

        found_calls = []
        processed_spans = set() # Keep track of spans already processed to avoid double-parsing

        # --- Helper Function for Parsing ---
        def parse_single_match(match, is_markdown):
             match_start, match_end = match.span()
             # Skip if this span overlaps with an already processed span
             for proc_start, proc_end in processed_spans:
                  if max(match_start, proc_start) < min(match_end, proc_end):
                      logger.debug(f"Skipping match at ({match_start}, {match_end}) as it overlaps with processed span ({proc_start}, {proc_end})")
                      return None

             xml_block_to_parse = ""; tool_name_from_outer_match = ""
             if is_markdown:
                 xml_block_to_parse = match.group(1).strip(); tool_name_from_outer_match = match.group(2)
             else:
                 xml_block_to_parse = match.group(0).strip(); tool_name_from_outer_match = match.group(1)

             try:
                 tool_name = next((name for name in self.manager.tool_executor.tools if name.lower() == tool_name_from_outer_match.lower()), None)
                 if not tool_name:
                     logger.warning(f"Agent {self.agent_id}: Found XML tag <{tool_name_from_outer_match}> but no matching tool is registered.")
                     return None

                 # Use re.search on the potentially fenced block
                 inner_match = self.raw_xml_tool_call_pattern.search(xml_block_to_parse)
                 if not inner_match:
                      logger.warning(f"Agent {self.agent_id}: Could not parse inner content of extracted block: '{xml_block_to_parse}'")
                      return None
                 inner_tool_name, inner_content = inner_match.groups()
                 if inner_tool_name.lower() != tool_name.lower():
                     logger.warning(f"Agent {self.agent_id}: Tool name mismatch between outer match ({tool_name}) and inner block ({inner_tool_name}).")
                     return None

                 logger.info(f"Agent {self.agent_id}: Detected valid call for tool '{tool_name}' at span ({match_start}, {match_end}) (Markdown fence: {is_markdown})")
                 tool_args = {}
                 param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"
                 param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                 for param_name, param_value_escaped in param_matches:
                      param_value = html.unescape(param_value_escaped.strip())
                      tool_args[param_name] = param_value

                 logger.info(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}")
                 processed_spans.add((match_start, match_end)) # Mark span as processed
                 return tool_name, tool_args, (match_start, match_end)

             except Exception as parse_err:
                 logger.error(f"Agent {self.agent_id}: Error parsing parameters for tool call '{xml_block_to_parse[:100]}...': {parse_err}", exc_info=True)
                 return None
        # --- End Helper Function ---


        # 1. Find all markdown fence matches
        markdown_matches = []
        if self.markdown_xml_tool_call_pattern:
            for m in self.markdown_xml_tool_call_pattern.finditer(buffer_content):
                parsed = parse_single_match(m, True)
                if parsed:
                    markdown_matches.append(parsed)

        # 2. Find all raw XML matches
        raw_matches = []
        if self.raw_xml_tool_call_pattern:
            for m in self.raw_xml_tool_call_pattern.finditer(buffer_content):
                parsed = parse_single_match(m, False)
                if parsed:
                     raw_matches.append(parsed) # We already checked for overlap in parse_single_match

        # Combine and sort by start index
        found_calls = markdown_matches + raw_matches
        found_calls.sort(key=lambda x: x[2][0]) # Sort by match_start_index

        if not found_calls:
             logger.debug(f"Agent {self.agent_id}: No valid tool calls found in buffer.")
        else:
             logger.info(f"Agent {self.agent_id}: Found {len(found_calls)} valid tool call(s) in buffer.")

        return found_calls
    # --- *** END MODIFIED XML Parsing Helper *** ---

    # --- Main Processing Logic ---
    async def process_message(self) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes the task based on the current message history using the LLM provider.
        Parses the response stream for XML tool calls (handling markdown) and yields requests.

        Yields:
            Dict[str, Any]: Events ('response_chunk', 'tool_requests', 'final_response', 'error', 'status').
        Receives:
             Optional[List[ToolResultDict]]: Currently ignored. Manager handles loop continuation.
        """
        # Check status - allow processing if idle OR awaiting override
        if self.status not in [AGENT_STATUS_IDLE, AGENT_STATUS_AWAITING_USER_OVERRIDE]:
            logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle or awaiting override (Status: {self.status}).")
            yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}
            return

        if not self.llm_provider:
            logger.error(f"Agent {self.agent_id}: LLM Provider not set.")
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}
            return
        if not self.manager:
             logger.error(f"Agent {self.agent_id}: Manager not set.")
             self.set_status(AGENT_STATUS_ERROR)
             yield {"type": "error", "content": "[Agent Error: Manager not configured]"}
             return

        self.set_status(AGENT_STATUS_PROCESSING)
        self.text_buffer = "" # Clear buffer at the start of processing
        complete_assistant_response = "" # Accumulate the full response for history/final event
        stream_had_error = False # Flag if an error event was yielded by the provider stream

        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(self.message_history)}")

        try:
            # 1. Ensure sandbox exists
            if not self.ensure_sandbox_exists():
                 self.set_status(AGENT_STATUS_ERROR)
                 yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 return

            # 2. Call the provider's stream_completion method (NO tools/tool_choice)
            provider_stream = self.llm_provider.stream_completion(
                messages=self.message_history, # Use current history
                model=self.model,
                temperature=self.temperature,
                # tools=None, # Explicitly None
                # tool_choice=None, # Explicitly None
                **self.provider_kwargs
            )

            # 3. Iterate through the provider's event stream
            async for event in provider_stream:
                event_type = event.get("type")

                if event_type == "response_chunk":
                    content = event.get("content", "")
                    if content:
                        # Append to buffer for XML parsing
                        self.text_buffer += content
                        complete_assistant_response += content # Add to full response tracker
                        # Yield intermediate text immediately
                        yield {"type": "response_chunk", "content": content}

                elif event_type == "status":
                    # Forward status messages from the provider
                    event["agent_id"] = self.agent_id
                    yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id
                    event["content"] = error_content
                    # DON'T set status to error here, let manager handle retries/override
                    stream_had_error = True # Flag that an error occurred
                    yield event # Yield the error event for the manager
                    # Don't break here, let manager decide based on error type
                else:
                    logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            # --- After the provider stream finishes (or error yielded) ---
            logger.debug(f"Agent {self.agent_id}: Provider stream finished processing. Stream had error: {stream_had_error}. Processing final buffer.")

            # Only parse tools or yield final_response if the stream didn't yield an error
            if not stream_had_error:
                # Now parse the completed buffer for *all* tool calls
                parsed_tool_calls = self._find_and_parse_tool_calls()

                if parsed_tool_calls:
                     logger.info(f"Agent {self.agent_id}: {len(parsed_tool_calls)} tool call(s) found in final buffer.")
                     tool_requests_list = []
                     for tool_name, tool_args, (match_start, match_end) in parsed_tool_calls:
                         call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                         tool_requests_list.append(
                             {"id": call_id, "name": tool_name, "arguments": tool_args}
                         )
                         # Small sleep to ensure unique call IDs if calls are very close
                         await asyncio.sleep(0.001)

                     self.set_status(AGENT_STATUS_AWAITING_TOOL)
                     logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} XML tool request(s).")
                     yield {
                         "type": "tool_requests",
                         "calls": tool_requests_list,
                         "raw_assistant_response": complete_assistant_response # Send full response for history
                     }

                else:
                     # No tool calls found in the final buffer
                     logger.debug(f"Agent {self.agent_id}: No tool calls found in final buffer.")
                     # Yield final_response event if response was generated
                     if complete_assistant_response:
                          logger.debug(f"Agent {self.agent_id}: Yielding final_response event (no tool calls).")
                          yield {"type": "final_response", "content": complete_assistant_response}
            else:
                 logger.warning(f"Agent {self.agent_id}: Skipping final tool parsing/response yielding because stream yielded an error.")


        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            # Don't set status to error, let manager handle it
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"} # Yield error for manager
        finally:
            # Set status to Idle only if it's currently Processing (and not awaiting override)
            if self.status == AGENT_STATUS_PROCESSING:
                self.set_status(AGENT_STATUS_IDLE)
            self.text_buffer = "" # Ensure buffer is cleared on exit
            logger.info(f"Agent {self.agent_id}: Finished processing cycle. Final Status: {self.status}")


    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent, including detailed status."""
        state = {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "status": self.status,
            "provider": self.provider_name,
            "model": self.model,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history),
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": False, # No longer directly used
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        # Include full config in state? Maybe just essential parts for UI status.
        # state["config"] = self.agent_config.get("config", {}) # Maybe too much?

        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info
        return state

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt."""
        logger.info(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        # Use the final combined system prompt (which might include tools/team info)
        self.message_history.append({"role": "system", "content": self.final_system_prompt})
