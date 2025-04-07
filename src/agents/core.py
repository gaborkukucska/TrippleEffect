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
AGENT_STATUS_AWAITING_USER_OVERRIDE = "awaiting_user_override" # <-- New Status

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
        if not tool_descriptions_xml.startswith("# Tools Description"): # Basic check to avoid double-adding
            self.final_system_prompt += "\n\n" + tool_descriptions_xml
        else:
             # If tool description is already part of the prompt (e.g. loaded session), just use it
             self.final_system_prompt = current_system_prompt

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

    # --- XML Parsing Helper ---
    def _find_and_parse_last_tool_call(self) -> Optional[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
        """
        Finds the *last* occurrence of a valid tool call (raw or fenced) in the text_buffer.
        Parses it and returns validated info.

        Returns:
            Optional[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
                (tool_name, tool_args, (match_start_index, match_end_index))
            Returns None if no valid call is found.
        """
        if not self.text_buffer:
            return None

        buffer_content = self.text_buffer # Work with the full buffer content
        logger.debug(f"Agent {self.agent_id}: Checking buffer for tool call (Length: {len(buffer_content)}): '{buffer_content[-250:]}'") # Log more chars

        last_match_info = None # Store (match_object, is_markdown_match)

        # 1. Find the last markdown fence match
        if self.markdown_xml_tool_call_pattern:
             last_md_match = None
             for m in self.markdown_xml_tool_call_pattern.finditer(buffer_content):
                 last_md_match = m
             if last_md_match:
                 last_match_info = (last_md_match, True)
                 logger.debug(f"Agent {self.agent_id}: Found last markdown fence match ending at {last_md_match.end()}.")

        # 2. Find the last raw XML match
        if self.raw_xml_tool_call_pattern:
             last_raw_match = None
             for m in self.raw_xml_tool_call_pattern.finditer(buffer_content):
                 last_raw_match = m
             if last_raw_match:
                 logger.debug(f"Agent {self.agent_id}: Found last raw XML match ending at {last_raw_match.end()}.")
                 # Only consider the raw match if no markdown match was found,
                 # OR if the raw match ends *later* than the markdown match
                 if last_match_info is None or last_raw_match.end() > last_match_info[0].end():
                     # Check if raw match is inside the markdown match - if so, ignore raw
                     if last_match_info and last_raw_match.start() >= last_match_info[0].start() and last_raw_match.end() <= last_match_info[0].end():
                         logger.debug("Raw match is inside markdown match, ignoring raw.")
                     else:
                         last_match_info = (last_raw_match, False)
                         logger.debug(f"Agent {self.agent_id}: Selecting raw XML match as the final candidate.")

        # 3. If no match found
        if not last_match_info:
            logger.debug(f"Agent {self.agent_id}: No valid tool call pattern found in buffer.")
            return None

        match, is_markdown = last_match_info
        match_start, match_end = match.span() # Get start/end indices of the full match

        # 4. Extract the core XML block and parse it
        xml_block_to_parse = ""
        tool_name_from_outer_match = "" # Tool name captured by the outer regex
        if is_markdown:
            # Group 1 contains the full XML block, Group 2 contains the tool name
            xml_block_to_parse = match.group(1).strip()
            tool_name_from_outer_match = match.group(2)
            logger.debug(f"Agent {self.agent_id}: Extracted XML from fence: '{xml_block_to_parse[:150]}...'")
        else:
            # For raw match, group(1) is tool name, group(0) is the full match block
            xml_block_to_parse = match.group(0).strip()
            tool_name_from_outer_match = match.group(1)
            logger.debug(f"Agent {self.agent_id}: Using raw XML block: '{xml_block_to_parse[:150]}...'")


        # 5. Validate tool name and parse parameters from the extracted block
        try:
            # Find the registered tool name (case-insensitive) using the name captured by the outer regex
            tool_name = next(
                (name for name in self.manager.tool_executor.tools if name.lower() == tool_name_from_outer_match.lower()),
                None
            )

            if not tool_name:
                logger.warning(f"Agent {self.agent_id}: Found XML tag <{tool_name_from_outer_match}> but no matching tool is registered.")
                return None

            # Now parse parameters from xml_block_to_parse
            # We need the inner content. Use the raw pattern again *on this block*
            # Use re.search here as .match only matches from the beginning
            inner_match = self.raw_xml_tool_call_pattern.search(xml_block_to_parse)
            if not inner_match:
                 logger.warning(f"Agent {self.agent_id}: Could not parse inner content of extracted block: '{xml_block_to_parse}'")
                 return None
            # Ensure the matched tool name inside the block is the same one we expected
            inner_tool_name, inner_content = inner_match.groups()
            if inner_tool_name.lower() != tool_name.lower():
                logger.warning(f"Agent {self.agent_id}: Tool name mismatch between outer match ({tool_name}) and inner block ({inner_tool_name}).")
                return None


            logger.info(f"Agent {self.agent_id}: Detected call for tool '{tool_name}' (Markdown fence: {is_markdown})")
            tool_args = {}
            # Parse parameters using regex on the inner content
            param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>"
            param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
            for param_name, param_value_escaped in param_matches:
                 param_value = html.unescape(param_value_escaped.strip())
                 tool_args[param_name] = param_value

            logger.info(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}")
            # Return tool_name, args, and the start/end indices of the *original full block* found
            return tool_name, tool_args, (match_start, match_end)

        except Exception as parse_err:
            logger.error(f"Agent {self.agent_id}: Error parsing parameters for tool call '{xml_block_to_parse[:100]}...': {parse_err}", exc_info=True)
            return None


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
        tool_call_yielded = False # Flag to track if a tool call was made this turn

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
                    # self.set_status(AGENT_STATUS_ERROR)
                    yield event # Yield the error event for the manager
                    # Don't break here, let manager decide based on error type
                    # break # Stop processing loop on provider error
                else:
                    logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            # --- After the provider stream finishes (or error yielded) ---
            logger.debug(f"Agent {self.agent_id}: Provider stream finished or yielded error. Processing final buffer.")

            # Check if an error was yielded by the stream loop above. If so, manager handles it.
            # We only proceed to parse tools or yield final_response if no error occurred.
            # How to check? The manager loop will break on error event. Here we just need to know if loop finished cleanly.
            # Let's assume if we reach here without the manager loop breaking, the stream finished *without* yielding an error event directly.

            # Now parse the completed buffer for the *last* tool call
            parsed_tool_info = self._find_and_parse_last_tool_call()

            if parsed_tool_info:
                 tool_name, tool_args, (match_start, match_end) = parsed_tool_info
                 logger.info(f"Agent {self.agent_id}: Final tool call found: {tool_name}, Span: ({match_start}, {match_end})")

                 # If there was text *before* the tool call that wasn't part of yielded chunks,
                 # this logic might be complex. Since we yielded all chunks, we assume the UI
                 # has the text. We just need to yield the tool request.
                 # The manager will add the `complete_assistant_response` (which includes the tool call text) to history.

                 call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                 self.set_status(AGENT_STATUS_AWAITING_TOOL)
                 logger.info(f"Agent {self.agent_id}: Yielding final XML tool request: ID={call_id}, Name={tool_name}, Args={tool_args}")
                 yield {
                     "type": "tool_requests",
                     "calls": [{"id": call_id, "name": tool_name, "arguments": tool_args}],
                     "raw_assistant_response": complete_assistant_response # Send full response for history
                 }
                 tool_call_yielded = True

            else:
                 # No tool call found in the final buffer
                 logger.debug(f"Agent {self.agent_id}: No tool call found in final buffer.")
                 # Since chunks were yielded, we only need to yield the final response event
                 # if there was any response generated at all.
                 if complete_assistant_response:
                      logger.debug(f"Agent {self.agent_id}: Yielding final_response event (no tool call).")
                      yield {"type": "final_response", "content": complete_assistant_response}


        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            # Don't set status to error, let manager handle it
            # self.set_status(AGENT_STATUS_ERROR)
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
