# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
import xml.etree.ElementTree as ET # For robust parsing (optional, regex might suffice for simple cases)
import html # For unescaping

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict # Removed ToolDict

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # No longer need ToolExecutor here
    from src.agents.manager import AgentManager

# Constants
# MAX_TOOL_CALLS_PER_TURN is handled by manager based on yields now

# Define possible agent statuses (remain the same)
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing" # General thinking/interacting with LLM
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result" # Waiting for manager to execute tool
AGENT_STATUS_EXECUTING_TOOL = "executing_tool" # Set by manager during execution
AGENT_STATUS_ERROR = "error"

# --- Regex for XML Tool Call Detection ---
# This pattern looks for <tool_name>...</tool_name> where tool_name is known.
# It's non-greedy (.*?) and captures the tool name and the inner content.
# It assumes tool names don't contain XML special characters.
# We'll compile this in __init__ based on available tools.
XML_TOOL_CALL_PATTERN = None # Will be set in __init__

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    parsing XML tool calls from responses, and yielding requests. Tracks its own status.
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
        # print(f"Agent {self.agent_id} Final System Prompt:\n{self.final_system_prompt[:500]}...") # Debug

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
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                 # Ensure tool names are regex-safe (though they should be simple)
                safe_tool_names = [re.escape(name) for name in tool_names]
                pattern = rf"<({'|'.join(safe_tool_names)})>([\s\S]*?)</\1>"
                # Compile with flags, making it case-insensitive
                self.xml_tool_call_pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
                print(f"Agent {self.agent_id}: Compiled XML tool pattern for tools: {tool_names}")
            else:
                self.xml_tool_call_pattern = None
                print(f"Agent {self.agent_id}: No tools found in executor, XML parsing disabled.")
        else:
            self.xml_tool_call_pattern = None
            print(f"Agent {self.agent_id}: Manager or ToolExecutor not available during init, XML parsing disabled.")


        print(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management (remains the same) ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        """Updates the agent's status and optionally tool info."""
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        if self.manager:
            asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else:
             print(f"Agent {self.agent_id}: Warning - Manager not set, cannot push status update.")


    # --- Dependency Setters ---
    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        # This might be redundant if manager is required in init, but keep for consistency
        self.manager = manager

    def set_tool_executor(self, tool_executor: Any): # Added type Any to match old signature
         # This method is no longer needed as tool executor is not used directly
         print(f"Agent {self.agent_id}: Warning - set_tool_executor called but ToolExecutor is no longer directly used by Agent.")
         pass

    def ensure_sandbox_exists(self) -> bool:
        """Creates the agent's sandbox directory if it doesn't exist."""
        try:
            self.sandbox_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            print(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}")
            return False

    # --- XML Parsing Helper ---
    def _parse_and_yield_xml_tool_call(self) -> Optional[Tuple[str, Dict[str, Any], int]]:
        """
        Checks the text_buffer for a complete XML tool call at the end.
        If found, parses it, validates against known tools, and returns info.

        Returns:
            Optional[Tuple[str, Dict[str, Any], int]]: (tool_name, tool_args, xml_block_length) if a valid call is found, else None.
            Returns None if no pattern matches or the match is incomplete/invalid.
        """
        if not self.xml_tool_call_pattern or not self.text_buffer.strip().endswith('>'):
            return None # No pattern compiled or buffer doesn't end with a potential tag close

        # Search for the pattern anywhere in the buffer for now
        # We expect it at the end, but let's be flexible initially
        match = self.xml_tool_call_pattern.search(self.text_buffer)

        if not match:
            # print(f"Agent {self.agent_id}: No XML tool pattern match in buffer: '{self.text_buffer[-100:]}'") # Debug
            return None

        # Check if the match occurs *reasonably* close to the end of the buffer.
        # This helps avoid matching old XML if the buffer wasn't cleared properly.
        # Allow for some trailing whitespace.
        if match.end() < len(self.text_buffer.rstrip()) - 5: # Allow 5 chars tolerance
             print(f"Agent {self.agent_id}: Found XML match, but not at the end of the buffer. Match ends at {match.end()}, buffer length {len(self.text_buffer)}. Ignoring.")
             return None # Match isn't at the expected position

        tool_name_match, inner_content = match.groups()
        xml_block = match.group(0) # The entire matched XML block <tool>...</tool>
        xml_block_length = len(xml_block)

        # Find the tool case-insensitively, but use the registered case
        tool_name = next(
            (name for name in self.manager.tool_executor.tools if name.lower() == tool_name_match.lower()),
            None
        )

        if not tool_name:
            print(f"Agent {self.agent_id}: Warning - Found XML tag <{tool_name_match}> but no matching tool is registered.")
            # Don't clear buffer yet, maybe it's just text
            return None

        print(f"Agent {self.agent_id}: Detected potential XML call for tool '{tool_name}'")
        tool_args = {}
        try:
            # Parse parameters using regex on the inner content - simpler than full XML parsing for known format
            param_pattern = r"<(\w+)>([\s\S]*?)</\1>"
            param_matches = re.findall(param_pattern, inner_content, re.DOTALL)
            for param_name, param_value_escaped in param_matches:
                 # Unescape HTML entities like <, >, &
                 param_value = html.unescape(param_value_escaped.strip())
                 tool_args[param_name] = param_value
            print(f"Agent {self.agent_id}: Parsed args for '{tool_name}': {tool_args}")
            return tool_name, tool_args, xml_block_length

        except Exception as parse_err:
            print(f"Agent {self.agent_id}: Error parsing parameters for tool '{tool_name}': {parse_err}")
            # Treat as invalid call, don't yield
            return None


    # --- Main Processing Logic ---
    async def process_message(self) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes the task based on the current message history using the LLM provider.
        Parses the response stream for XML tool calls and yields requests to the manager.

        Yields:
            Dict[str, Any]: Events ('response_chunk', 'tool_requests', 'final_response', 'error', 'status').
                            'tool_requests' contains parsed arguments and a generated call_id.
                            'final_response' contains the complete assistant text for history.

        Receives:
             Optional[List[ToolResultDict]]: Placeholder for potential future use, currently unused as manager handles the loop continuation. Sent value is ignored.
        """
        if self.status != AGENT_STATUS_IDLE:
            print(f"Agent {self.agent_id} is not idle (Status: {self.status}). Ignoring message processing request.")
            # Manager already added user msg to history, so just yield error and return
            yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}
            return

        if not self.llm_provider:
            print(f"Agent {self.agent_id}: LLM Provider not set.")
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}
            return
        if not self.manager:
             print(f"Agent {self.agent_id}: Manager not set.")
             self.set_status(AGENT_STATUS_ERROR)
             yield {"type": "error", "content": "[Agent Error: Manager not configured]"}
             return


        self.set_status(AGENT_STATUS_PROCESSING)
        self.text_buffer = "" # Clear buffer at the start of processing
        complete_assistant_response = "" # Accumulate the full response for history/final event

        print(f"Agent {self.agent_id} starting processing via {self.provider_name}. Current history length: {len(self.message_history)}")

        try:
            # 1. Ensure sandbox exists
            if not self.ensure_sandbox_exists():
                 self.set_status(AGENT_STATUS_ERROR)
                 yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 return

            # 2. Call the provider's stream_completion method (NO tools/tool_choice)
            # Provider needs the current history.
            provider_stream = self.llm_provider.stream_completion(
                messages=self.message_history, # Use current history
                model=self.model,
                temperature=self.temperature,
                # tools=None, # Explicitly None
                # tool_choice=None, # Explicitly None
                # Pass any additional kwargs specific to this agent's config
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
                             tool_name, tool_args, xml_len = parsed_tool_info

                             # Yield text preceding the XML block
                             preceding_text = self.text_buffer[:-xml_len]
                             if preceding_text:
                                 yield {"type": "response_chunk", "content": preceding_text}
                                 # print(f"Agent {self.agent_id}: Yielded preceding text: '{preceding_text[:50]}...'") # Debug

                             # Generate a unique call ID for this XML request
                             call_id = f"xml_call_{self.agent_id}_{int(time.time())}_{os.urandom(2).hex()}"

                             # Yield the tool request to the manager
                             self.set_status(AGENT_STATUS_AWAITING_TOOL)
                             print(f"Agent {self.agent_id}: Yielding XML tool request: ID={call_id}, Name={tool_name}, Args={tool_args}")
                             yield {
                                 "type": "tool_requests",
                                 "calls": [{"id": call_id, "name": tool_name, "arguments": tool_args}],
                                 "raw_assistant_response": complete_assistant_response # Send full text up to this point
                             }

                             # Clear the buffer after yielding request (including the XML part)
                             self.text_buffer = ""
                             # Status will be updated by manager / upon receiving results

                             # --- Important Note ---
                             # The agent's job for this turn might be done after yielding the tool request.
                             # It awaits the manager's loop to continue. The `asend()` mechanism
                             # is not used here in the same way as when the *provider* handled tools.
                             # The generator state is implicitly saved by the `yield`.
                             # When the manager's loop calls `asend(results)` later, the generator resumes
                             # *after* this yield point, receives the results (which we currently ignore here),
                             # and continues processing any further stream events from the provider.

                        # else: # No complete tool found yet, yield the current chunk if buffer isn't just XML starter
                             if not self.text_buffer.strip().startswith('<') or len(self.text_buffer) > 50: # Heuristic to avoid yielding partial tags alone
                                 # Yield the text accumulated so far if no tool call was made
                                 # Avoid yielding if buffer only contains potential start of XML
                                  yield {"type": "response_chunk", "content": self.text_buffer}
                                  # Clear buffer after yielding non-tool text
                                  self.text_buffer = ""


                elif event_type == "status":
                    event["agent_id"] = self.agent_id
                    yield event
                elif event_type == "error":
                    event["agent_id"] = self.agent_id
                    event["content"] = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    self.set_status(AGENT_STATUS_ERROR)
                    yield event
                    print(f"Agent {self.agent_id}: Received error event from provider, stopping.")
                    break # Stop processing loop on provider error
                else:
                    print(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            # --- After the provider stream finishes ---
            # Yield any remaining text in the buffer that wasn't part of a tool call
            if self.text_buffer:
                 # Check again for a tool call in case the stream ended exactly after one
                 parsed_tool_info = self._parse_and_yield_xml_tool_call()
                 if parsed_tool_info:
                      tool_name, tool_args, xml_len = parsed_tool_info
                      preceding_text = self.text_buffer[:-xml_len]
                      if preceding_text:
                           yield {"type": "response_chunk", "content": preceding_text}
                      call_id = f"xml_call_{self.agent_id}_{int(time.time())}_{os.urandom(2).hex()}"
                      self.set_status(AGENT_STATUS_AWAITING_TOOL)
                      print(f"Agent {self.agent_id}: Yielding final XML tool request: ID={call_id}, Name={tool_name}, Args={tool_args}")
                      yield {
                          "type": "tool_requests",
                          "calls": [{"id": call_id, "name": tool_name, "arguments": tool_args}],
                          "raw_assistant_response": complete_assistant_response
                      }
                      self.text_buffer = "" # Clear buffer
                 else:
                      # No tool call at the very end, yield remaining text
                      yield {"type": "response_chunk", "content": self.text_buffer}
                      self.text_buffer = ""


            # Yield the complete response for the manager to add to history
            # Only yield if there was some response content generated.
            if complete_assistant_response:
                 yield {"type": "final_response", "content": complete_assistant_response}

            print(f"Agent {self.agent_id}: Provider stream finished.")

        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            traceback.print_exc()
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            # Set status to Idle unless it's already Error or awaiting tool result
            if self.status not in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_TOOL]:
                self.set_status(AGENT_STATUS_IDLE)
            self.text_buffer = "" # Ensure buffer is cleared on exit
            print(f"Agent {self.agent_id}: Finished processing cycle. Final Status: {self.status}")


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
            "llm_provider_info": repr(self.llm_provider),
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": False, # No longer directly used
            "xml_tool_parsing_enabled": self.xml_tool_call_pattern is not None
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info
        return state

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        # Use the final combined system prompt
        self.message_history.append({"role": "system", "content": self.final_system_prompt})
