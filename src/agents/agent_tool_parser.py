# START OF FILE src/agents/agent_tool_parser.py
import re
import html
import logging
from typing import List, Dict, Tuple, Any, Optional, Pattern, Union # Added Union
import xml.etree.ElementTree as ET # Import ElementTree for robust parsing

# Import BaseTool definition for type hinting tool schema
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Define the structure for parsing errors explicitly for clarity
ParsingErrorDict = Dict[str, Union[str, bool, Tuple[int, int]]]
# Define the structure for valid calls explicitly
ValidCallTuple = Tuple[str, Dict[str, Any], Tuple[int, int]]

def find_and_parse_xml_tool_calls(
    text_buffer: str,
    tools: Dict[str, BaseTool], # Pass the registered tools dict
    # These are the compiled patterns from Agent Core
    agent_core_raw_xml_pattern: Optional[Pattern],
    agent_core_markdown_xml_pattern: Optional[Pattern],
    agent_id: str # For logging
    ) -> Dict[str, Union[List[ValidCallTuple], List[ParsingErrorDict]]]: # Updated return type
    """
    Finds *all* occurrences of valid XML tool calls (raw or fenced)
    in the text_buffer, avoiding nested matches. Parses them and returns validated info.
    Returns a dictionary with 'valid_calls' and 'parsing_errors'.
    Uses ElementTree for more robust XML parsing.
    """
    if not text_buffer:
        return {"valid_calls": [], "parsing_errors": []}

    logger.debug(f"Checking buffer for XML tool calls for agent {agent_id}.")

    found_calls_details: List[ValidCallTuple] = []
    parsing_errors: List[ParsingErrorDict] = []
    processed_spans = set()

    def is_overlapping(start, end, existing_spans):
        for proc_start, proc_end in existing_spans:
            if max(start, proc_start) < min(end, proc_end):
                return True
        return False

    # This helper parses a single, isolated XML block assuming it's a complete tool call
    def parse_isolated_xml_block(xml_block: str, identified_tool_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]: # Updated return type
        tool_args = {}
        error_message: Optional[str] = None
        cleaned_xml_block = xml_block # Keep original for error reporting if cleaning fails

        try:
            # Attempt to clean up common LLM mistakes like extra text before/after XML
            # This is a basic cleanup; more sophisticated might be needed if issues persist.
            if not xml_block.startswith("<"):
                start_tag_index = xml_block.find(f"<{identified_tool_name}")
                if start_tag_index != -1:
                    cleaned_xml_block = xml_block[start_tag_index:]
                else: # If the expected start tag is not found at all
                    cleaned_xml_block = xml_block # Keep original if specific start tag not found
            
            # Ensure it ends with the correct closing tag, remove trailing text
            end_tag = f"</{identified_tool_name}>"
            end_tag_index = cleaned_xml_block.rfind(end_tag)
            if end_tag_index != -1:
                cleaned_xml_block = cleaned_xml_block[:end_tag_index + len(end_tag)]
            else: # If no proper end tag, parsing will likely fail, but log it
                # This warning is now less critical as the error will be returned
                logger.debug(f"[PARSE_HELPER] XML block for '{identified_tool_name}' might be malformed (missing/incorrect end tag): '{xml_block[:100]}...'")


            root = ET.fromstring(cleaned_xml_block)
            if root.tag.lower() != identified_tool_name.lower():
                error_message = f"XML root tag '{root.tag}' does not match identified tool name '{identified_tool_name}'."
                logger.warning(f"[PARSE_HELPER] {error_message}")
                return None, error_message

            for child in root:
                param_name = child.tag
                param_value = child.text.strip() if child.text else ""
                tool_args[param_name] = html.unescape(param_value)
            return tool_args, None # Success
        except ET.ParseError as e:
            error_message = f"XML ParseError: {str(e)}"
            logger.error(f"[PARSE_HELPER] Error for tool '{identified_tool_name}': {error_message}. Block: '{xml_block[:200]}...' Attempted Cleaned: '{cleaned_xml_block[:200]}...'")
            return None, error_message
        except Exception as e:
            error_message = f"Unexpected error parsing XML: {str(e)}"
            logger.error(f"[PARSE_HELPER] Error for tool '{identified_tool_name}': {error_message}. Block: '{xml_block[:200]}...' Attempted Cleaned: '{cleaned_xml_block[:200]}...'")
            return None, error_message

    matches_to_process = []

    # 1. Find Markdown-Fenced XML Tool Calls
    if agent_core_markdown_xml_pattern:
        for match in agent_core_markdown_xml_pattern.finditer(text_buffer):
            full_xml_content_in_fence = match.group(1).strip()
            tool_name_candidate_from_regex = match.group(2).lower()
            matches_to_process.append({
                "span": match.span(),
                "xml_block": full_xml_content_in_fence,
                "tool_name_candidate": tool_name_candidate_from_regex,
                "is_markdown": True
            })

    # 2. Find Raw XML Tool Calls (non-markdown fenced)
    if agent_core_raw_xml_pattern:
        for match in agent_core_raw_xml_pattern.finditer(text_buffer):
            full_xml_content_raw = match.group(0).strip()
            tool_name_candidate_from_regex = match.group(1).lower()
            matches_to_process.append({
                "span": match.span(),
                "xml_block": full_xml_content_raw,
                "tool_name_candidate": tool_name_candidate_from_regex,
                "is_markdown": False
            })

    matches_to_process.sort(key=lambda m: m["span"][0])

    for item in matches_to_process:
        match_span = item["span"]
        xml_block_to_parse = item["xml_block"]
        tool_name_candidate = item["tool_name_candidate"]
        is_markdown = item["is_markdown"]

        if is_overlapping(match_span[0], match_span[1], processed_spans):
            continue

        actual_tool_name = next((name for name in tools if name.lower() == tool_name_candidate), None)
        if not actual_tool_name:
            logger.warning(f"Regex matched unknown tool candidate '{tool_name_candidate}' for agent {agent_id}. Skipping.")
            continue

        logger.info(f"Detected tool call for '{actual_tool_name}' for agent {agent_id}.")
        
        parsed_args, error_detail = parse_isolated_xml_block(xml_block_to_parse, actual_tool_name)

        if parsed_args is not None:
            tool_schema = tools[actual_tool_name].get_schema()
            expected_params_schema = {p['name'].lower(): p for p in tool_schema.get('parameters', [])}
            provided_arg_keys_lower = {k.lower() for k in parsed_args.keys()}
            missing_required_params = []
            for p_name_lower_schema, p_info_schema in expected_params_schema.items():
                if p_info_schema.get('required', True) and p_name_lower_schema not in provided_arg_keys_lower:
                    missing_required_params.append(p_info_schema['name'])

            if missing_required_params:
                logger.warning(f"Tool '{actual_tool_name}' call from agent {agent_id} is missing required parameters: {missing_required_params}.")

            found_calls_details.append((actual_tool_name, parsed_args, match_span))
            processed_spans.add(match_span)
        else:
            logger.warning(f"Failed to parse XML block for tool '{actual_tool_name}' for agent {agent_id}. Error: {error_detail}")
            parsing_errors.append({
                "tool_name": actual_tool_name,
                "error_message": error_detail or "Unknown parsing error",
                "xml_block": xml_block_to_parse,
                "is_markdown": is_markdown,
                "span": match_span
            })
            processed_spans.add(match_span)

    if found_calls_details:
        logger.info(f"Found {len(found_calls_details)} valid tool calls for agent {agent_id}.")
    if parsing_errors:
         logger.warning(f"Encountered {len(parsing_errors)} XML parsing errors for agent {agent_id}.")

    return {"valid_calls": found_calls_details, "parsing_errors": parsing_errors}
# END OF FILE src/agents/agent_tool_parser.py