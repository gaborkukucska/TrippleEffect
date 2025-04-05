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

# --- Regex for XML Tool Call Detection ---
# We'll compile this in __init__ based on available tools.
XML_TOOL_CALL_PATTERN = None # Will be set in __init__

# --- Regex to potentially find XML within markdown code fences ---
# Looks for ```[optional language specifier]\n<tool_name>...</tool_name>\n```
# It's non-greedy and captures the tool name and the inner content *of the XML part*.
# Assumes the XML block is the primary content within the fence.
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
        original_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}

        # Injected dependencies
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager # Manager is essential now

        # Combine original prompt with dynamic tool descriptions
        self.final_system_prompt: str = original_system_prompt + "\n\n" + tool_descriptions_xml
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

                # Pattern for raw XML block: <tool_name>...</tool_name>
                raw_pattern_str = rf"<({tool_names_pattern_group})>([\s\S]*?)</\1>"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)

                # Pattern for XML within markdown fence
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

    # --- Status Management (remains the same) ---
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
    def _parse_and_yield_xml_tool_call(self) -> Optional[Tuple[str, Dict[str, Any], int]]:
        """
        Checks the text_buffer for a complete XML tool call at the end,
        potentially wrapped in markdown fences.
        If found, parses it, validates against known tools, and returns info.

        Returns:
            Optional[Tuple[str, Dict[str, Any], int]]: (tool_name, tool_args, block_length)
            where block_length is the length of the entire matched block (including fences).
            Returns None if no valid call is found.
        """
        buffer_trimmed = self.text_buffer.rstrip() # Work with trimmed buffer for checks
        if not buffer_trimmed:
            return None

        logger.debug(f"Agent {self.agent_id}: Checking buffer for tool call (last 150 chars): '{buffer_trimmed[-150:]}'")

        match = None
        matched_xml_block = None
        block_length = 0
        is_markdown_match = False

        # 1. Check for XML within markdown fences first
        if self.markdown_xml_tool_call_pattern:
             # Search backwards from the end for the pattern
             iterator = self.markdown_xml_tool_call_pattern.finditer(self.text_buffer)
             last_match = None
             for m in iterator:
                 last_match = m
             match = last_match

             if match and match.end() >= len(buffer_trimmed) - 5: # Allow slight tolerance from the end
                 # Matched the fenced pattern near the end
                 matched_xml_block = match.group(1) # Group 1 captured the inner <tool>...</tool>
                 block_length = len(match.group(0)) # Length of the whole ```...``` block
                 is_markdown_match = True
                 logger.debug(f"Agent {self.agent_id}: Found potential tool call within markdown fence near end.")
             else:
                 match = None # Reset match if not near the end

        # 2. If no markdown match, check for raw XML block near the end
        if not match and self.raw_xml_tool_call_pattern:
             # Search backwards from the end
             iterator = self.raw_xml_tool_call_pattern.finditer(self.text_buffer)
             last_match = None
             for m in iterator:
                 last_match = m
             match = last_match

             if match and match.end() >= len(buffer_trimmed) - 5: # Allow slight tolerance
                 matched_xml_block = match.group(0) # Group 0 is the whole <tool>...</tool> block
                 block_length = len(matched_xml_block)
                 is_markdown_match = False
                 logger.debug(f"Agent {self.agent_id}: Found potential raw tool call near end.")
             else:
                  match = None # Reset match if not near the end


        # 3. If no match found near the end
        if not match or not matched_xml_block:
            # logger.debug(f"Agent {self.agent_id}: No complete tool call pattern found near end of buffer.")
            return None

        # 4. Parse the identified XML block (which is `matched_xml_block`)
        try:
            # Use regex again on the *extracted* XML block to get tool name and inner content
            inner_match = self.raw_xml_tool_call_pattern.match(matched_xml_block.strip())
            if not inner_match:
                 logger.warning(f"Agent {self.agent_id}: Could not re-match tool name pattern within extracted block: '{matched_xml_block}'")
                 return None

            tool_name_match, inner_content = inner_match.groups()

            # Find the registered tool name (case-insensitive)
            tool_name = next(
                (name for name in self.manager.tool_executor.tools if name.lower() == tool_name_match.lower()),
                None
            )

            if not tool_name:
                logger.warning(f"Agent {self.agent_id}: Found XML tag <{tool_name_match}> but no matching tool is registered.")
                return None

            logger.info(f"Agent {self.agent_id}: Detected call for tool '{tool_name}' (Markdown fence: {is_markdown_match})")
            tool_args = {}
            # Parse parameters using regex on the inner content
            # Pattern: <param_name>param_value</param_name> (non-greedy value)
            param_pattern = r"<(\w+?)\s*>([\s\S]*?)</\1>" # Allow optional attributes in tag if needed: <(\w+?)(?:\s+.*?)?>
            param_matches = re.findall(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
            for param_name, param_value_escaped in param_matches:
                 # Unescape HTML entities like <, >, & just in case
                 param_value = html.unescape(param_value_escaped.strip())
                 tool_args[param_name] = param_value

            logger.info(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}")
            # Return tool_name, args, and the length of the *original* block found (incl. fences)
            return tool_name, tool_args, block_length

        except Exception as parse_err:
            logger.error(f"Agent {self.agent_id}: Error parsing parameters for tool call '{matched_xml_block[:100]}...': {parse_err}", exc_info=True)
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
        if self.status != AGENT_STATUS_IDLE:
            logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle (Status: {self.status}).")
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

                        # Attempt to parse XML tool call from the updated buffer
                        parsed_tool_info = self._parse_and_yield_xml_tool_call()

                        if parsed_tool_info:
                             tool_name, tool_args, block_len = parsed_tool_info

                             # Yield text preceding the tool call block
                             preceding_text = self.text_buffer[:-block_len]
                             if preceding_text:
                                 logger.debug(f"Agent {self.agent_id}: Yielding preceding text: '{preceding_text[:50]}...'")
                                 yield {"type": "response_chunk", "content": preceding_text}
                                 # Clear buffer up to the tool call
                                 self.text_buffer = self.text_buffer[-block_len:]
                             else:
                                 # If the buffer *only* contained the tool call block
                                 self.text_buffer = "" # Clear buffer completely


                             # Generate a unique call ID
                             call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"

                             # Yield the tool request to the manager
                             self.set_status(AGENT_STATUS_AWAITING_TOOL)
                             logger.info(f"Agent {self.agent_id}: Yielding XML tool request: ID={call_id}, Name={tool_name}, Args={tool_args}")
                             yield {
                                 "type": "tool_requests",
                                 "calls": [{"id": call_id, "name": tool_name, "arguments": tool_args}],
                                 # Send full text up to this point for manager history
                                 "raw_assistant_response": complete_assistant_response
                             }
                             tool_call_yielded = True

                             # Clear the buffer after yielding request (it now only contains the parsed block or is empty)
                             self.text_buffer = ""
                             # Generator execution pauses here until manager sends result (or None) via asend()

                        else:
                             # No complete tool found yet. Yield the current buffer content as a chunk.
                             # This assumes that if a tool call *is* coming, it will be the last thing.
                             if self.text_buffer:
                                  logger.debug(f"Agent {self.agent_id}: No tool call detected yet, yielding text chunk: '{self.text_buffer[:50]}...'")
                                  yield {"type": "response_chunk", "content": self.text_buffer}
                                  # Clear buffer after yielding non-tool text
                                  self.text_buffer = ""


                elif event_type == "status":
                    # Forward status messages from the provider
                    event["agent_id"] = self.agent_id
                    yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id
                    event["content"] = error_content
                    self.set_status(AGENT_STATUS_ERROR)
                    yield event
                    break # Stop processing loop on provider error
                else:
                    logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            # --- After the provider stream finishes ---
            logger.debug(f"Agent {self.agent_id}: Provider stream finished.")

            # Check remaining buffer content one last time ONLY if no tool call was yielded during the stream
            if not tool_call_yielded and self.text_buffer:
                 logger.debug(f"Agent {self.agent_id}: Checking final buffer content...")
                 parsed_tool_info = self._parse_and_yield_xml_tool_call()
                 if parsed_tool_info:
                      tool_name, tool_args, block_len = parsed_tool_info
                      preceding_text = self.text_buffer[:-block_len]
                      if preceding_text:
                           logger.debug(f"Agent {self.agent_id}: Yielding final preceding text: '{preceding_text[:50]}...'")
                           yield {"type": "response_chunk", "content": preceding_text}

                      call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                      self.set_status(AGENT_STATUS_AWAITING_TOOL)
                      logger.info(f"Agent {self.agent_id}: Yielding final XML tool request: ID={call_id}, Name={tool_name}, Args={tool_args}")
                      yield {
                          "type": "tool_requests",
                          "calls": [{"id": call_id, "name": tool_name, "arguments": tool_args}],
                          "raw_assistant_response": complete_assistant_response
                      }
                      tool_call_yielded = True
                      self.text_buffer = "" # Clear buffer
                 else:
                      # No tool call at the very end, yield remaining text
                      logger.debug(f"Agent {self.agent_id}: Yielding final remaining text: '{self.text_buffer[:50]}...'")
                      yield {"type": "response_chunk", "content": self.text_buffer}
                      self.text_buffer = ""

            # Yield the complete assistant response text for the manager to add to history,
            # but only if *no* tool call was made (manager adds history *before* tool execution otherwise).
            if not tool_call_yielded and complete_assistant_response:
                 logger.debug(f"Agent {self.agent_id}: Yielding final_response event.")
                 yield {"type": "final_response", "content": complete_assistant_response}


        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            # Set status to Idle only if it's currently Processing
            # (Avoid overriding Error or Awaiting Tool Result statuses)
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
            # Avoid logging potentially sensitive provider info directly
            # "llm_provider_info": repr(self.llm_provider),
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": False, # No longer directly used
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info
        return state

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt."""
        logger.info(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        # Use the final combined system prompt
        self.message_history.append({"role": "system", "content": self.final_system_prompt})
