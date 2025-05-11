# START OF FILE src/agents/agent_tool_parser.py
import re
import html
import logging
from typing import List, Dict, Tuple, Any, Optional, Pattern
import xml.etree.ElementTree as ET # Import ElementTree for robust parsing

# Import BaseTool definition for type hinting tool schema
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

def find_and_parse_xml_tool_calls(
    text_buffer: str,
    tools: Dict[str, BaseTool], # Pass the registered tools dict
    # These are the compiled patterns from Agent Core
    agent_core_raw_xml_pattern: Optional[Pattern],
    agent_core_markdown_xml_pattern: Optional[Pattern],
    agent_id: str # For logging
    ) -> List[Tuple[str, Dict[str, Any], Tuple[int, int]]]:
    """
    Finds *all* occurrences of valid XML tool calls (raw or fenced)
    in the text_buffer, avoiding nested matches. Parses them and returns validated info.
    Uses ElementTree for more robust XML parsing.
    """
    if not text_buffer: return []
    buffer_content_for_logging = text_buffer.strip() # For logging only
    logger.debug(f"Agent {agent_id}: [PARSE_DEBUG] Checking stripped buffer for XML tool calls (Len: {len(buffer_content_for_logging)}):\n>>>\n{buffer_content_for_logging}\n<<<")

    found_calls_details = []
    processed_spans = set()

    def is_overlapping(start, end, existing_spans):
        for proc_start, proc_end in existing_spans:
            if max(start, proc_start) < min(end, proc_end):
                return True
        return False

    # This helper parses a single, isolated XML block assuming it's a complete tool call
    def parse_isolated_xml_block(xml_block: str, identified_tool_name: str) -> Optional[Dict[str, Any]]:
        tool_args = {}
        try:
            # Attempt to clean up common LLM mistakes like extra text before/after XML
            # This is a basic cleanup; more sophisticated might be needed if issues persist.
            cleaned_xml_block = xml_block
            if not xml_block.startswith("<"):
                start_tag_index = xml_block.find(f"<{identified_tool_name}")
                if start_tag_index != -1:
                    cleaned_xml_block = xml_block[start_tag_index:]
            
            # Ensure it ends with the correct closing tag, remove trailing text
            end_tag = f"</{identified_tool_name}>"
            end_tag_index = cleaned_xml_block.rfind(end_tag)
            if end_tag_index != -1:
                cleaned_xml_block = cleaned_xml_block[:end_tag_index + len(end_tag)]
            else: # If no proper end tag, parsing will likely fail, but log it
                logger.warning(f"[PARSE_HELPER] XML block for '{identified_tool_name}' might be malformed (missing/incorrect end tag): '{xml_block[:100]}...'")


            root = ET.fromstring(cleaned_xml_block)
            if root.tag.lower() != identified_tool_name.lower():
                logger.warning(f"[PARSE_HELPER] XML root tag '{root.tag}' does not match identified tool name '{identified_tool_name}'.")
                return None # Mismatch

            for child in root:
                param_name = child.tag
                param_value = child.text.strip() if child.text else ""
                tool_args[param_name] = html.unescape(param_value)
            return tool_args
        except ET.ParseError as e:
            logger.error(f"[PARSE_HELPER] XML ParseError for tool '{identified_tool_name}': {e}. Block: '{xml_block[:200]}...' Cleaned: '{cleaned_xml_block[:200]}...'")
            return None
        except Exception as e:
            logger.error(f"[PARSE_HELPER] Unexpected error parsing XML for tool '{identified_tool_name}': {e}. Block: '{xml_block[:200]}...' Cleaned: '{cleaned_xml_block[:200]}...'")
            return None

    matches_to_process = []

    # 1. Find Markdown-Fenced XML Tool Calls
    if agent_core_markdown_xml_pattern:
        for match in agent_core_markdown_xml_pattern.finditer(text_buffer):
            # Group 1 of agent_core_markdown_xml_pattern is the full inner XML block
            # Group 2 is the tool name from the regex pattern
            full_xml_content_in_fence = match.group(1).strip()
            tool_name_candidate_from_regex = match.group(2).lower()
            matches_to_process.append({
                "span": match.span(), # Overall span of the markdown block
                "xml_block": full_xml_content_in_fence, # The <tool>...</tool> part
                "tool_name_candidate": tool_name_candidate_from_regex,
                "is_markdown": True
            })

    # 2. Find Raw XML Tool Calls (non-markdown fenced)
    if agent_core_raw_xml_pattern:
        for match in agent_core_raw_xml_pattern.finditer(text_buffer):
            # Group 0 is the full match "<tool>content</tool>"
            # Group 1 is the tool name
            full_xml_content_raw = match.group(0).strip()
            tool_name_candidate_from_regex = match.group(1).lower()
            matches_to_process.append({
                "span": match.span(),
                "xml_block": full_xml_content_raw,
                "tool_name_candidate": tool_name_candidate_from_regex,
                "is_markdown": False
            })

    # Sort all found matches by their start position to handle them in order and filter overlaps
    matches_to_process.sort(key=lambda m: m["span"][0])

    for item in matches_to_process:
        match_span = item["span"]
        xml_block_to_parse = item["xml_block"]
        # Use the tool name captured by the regex group specific to tool names
        tool_name_candidate = item["tool_name_candidate"]
        is_markdown = item["is_markdown"]

        if is_overlapping(match_span[0], match_span[1], processed_spans):
            logger.debug(f"[PARSE_DEBUG] Skipping overlapping match at span {match_span} for candidate '{tool_name_candidate}'.")
            continue

        # Find the actual registered tool name (case-insensitive comparison with regex-captured name)
        actual_tool_name = next((name for name in tools if name.lower() == tool_name_candidate), None)
        if not actual_tool_name:
            logger.warning(f"[PARSE_DEBUG] Agent {agent_id}: Regex matched candidate <{tool_name_candidate}> but no such tool is registered. Skipping.")
            continue

        logger.info(f"Agent {agent_id}: Detected call for tool '{actual_tool_name}' (candidate: '{tool_name_candidate}') at span {match_span} (Markdown: {is_markdown})")
        
        parsed_args = parse_isolated_xml_block(xml_block_to_parse, actual_tool_name)

        if parsed_args is not None:
            # Schema validation for required parameters
            tool_schema = tools[actual_tool_name].get_schema()
            expected_params_schema = {p['name'].lower(): p for p in tool_schema.get('parameters', [])}
            provided_arg_keys_lower = {k.lower() for k in parsed_args.keys()}
            missing_required_params = []
            for p_name_lower_schema, p_info_schema in expected_params_schema.items():
                if p_info_schema.get('required', True) and p_name_lower_schema not in provided_arg_keys_lower:
                    missing_required_params.append(p_info_schema['name']) # Report original case name

            if missing_required_params:
                logger.warning(f"Agent {agent_id}: Tool '{actual_tool_name}' call MISSING required parameter(s) defined in schema: {missing_required_params}. Tool execution will likely fail if these are truly needed by the tool's logic.")

            found_calls_details.append((actual_tool_name, parsed_args, match_span))
            processed_spans.add(match_span)
        else:
            logger.warning(f"Agent {agent_id}: Failed to parse XML block for tool '{actual_tool_name}' at span {match_span} using ElementTree.")


    if not found_calls_details:
        logger.debug(f"Agent {agent_id}: [PARSE_DEBUG] No valid XML tool calls found in buffer after full processing.")
    else:
        logger.info(f"Agent {agent_id}: Found {len(found_calls_details)} valid XML tool call(s) in buffer.")
    return found_calls_details