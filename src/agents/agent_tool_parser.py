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
    if not text_buffer: return {"valid_calls": [], "parsing_errors": []}
    buffer_content_for_logging = text_buffer.strip() # For logging only
    logger.debug(f"Agent {agent_id}: [PARSE_DEBUG] Checking stripped buffer for XML tool calls (Len: {len(buffer_content_for_logging)}):\n>>>\n{buffer_content_for_logging}\n<<<")

    found_calls_details: List[ValidCallTuple] = []
    parsing_errors: List[ParsingErrorDict] = [] # Initialize parsing_errors list
    processed_spans = set()

    def is_overlapping(start, end, existing_spans):
        for proc_start, proc_end in existing_spans:
            if max(start, proc_start) < min(end, proc_end):
                return True
        return False

    def _sanitize_xml_block(xml_block: str, tool_name: str) -> str:
        """Enhanced XML sanitization to handle common LLM-generated malformations."""
        cleaned = xml_block.strip()
        
        # Remove common prefixes that break XML parsing
        prefixes_to_remove = ['```xml', '```', 'xml']
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        
        # Remove common suffixes
        suffixes_to_remove = ['```', '`']
        for suffix in suffixes_to_remove:
            if cleaned.lower().endswith(suffix.lower()):
                cleaned = cleaned[:-len(suffix)].strip()
        
        # Ensure proper start tag
        if not cleaned.startswith("<"):
            start_tag_index = cleaned.find(f"<{tool_name}")
            if start_tag_index != -1:
                cleaned = cleaned[start_tag_index:]
            else:
                # Try case-insensitive search
                start_tag_index = cleaned.lower().find(f"<{tool_name.lower()}")
                if start_tag_index != -1:
                    cleaned = cleaned[start_tag_index:]
        
        # Ensure proper end tag and remove trailing content
        expected_end_tag = f"</{tool_name}>"
        end_tag_index = cleaned.rfind(expected_end_tag)
        if end_tag_index == -1:
            # Try case-insensitive search
            end_tag_index = cleaned.lower().rfind(f"</{tool_name.lower()}>")
            if end_tag_index != -1:
                # Find the actual end tag with correct case
                actual_end_start = cleaned.rfind("<", 0, end_tag_index + len(expected_end_tag))
                if actual_end_start != -1:
                    cleaned = cleaned[:actual_end_start] + expected_end_tag
        else:
            cleaned = cleaned[:end_tag_index + len(expected_end_tag)]
        
        # Handle XML entities that might be double-escaped
        cleaned = cleaned.replace("&amp;lt;", "&lt;").replace("&amp;gt;", "&gt;")
        
        return cleaned

    def _generate_corrected_xml_example(tool_name: str, tools: Dict[str, BaseTool]) -> str:
        """Generate a corrected XML example for the tool."""
        if tool_name not in tools:
            return f"<{tool_name}><action>example_action</action></{tool_name}>"
        
        tool_schema = tools[tool_name].get_schema()
        params = tool_schema.get('parameters', [])
        
        example_parts = [f"<{tool_name}>"]
        for param in params[:3]:  # Show first 3 parameters as example
            param_name = param['name']
            if param['type'] == 'string':
                example_value = f"example_{param_name}"
            elif param['type'] == 'integer':
                example_value = "1"
            elif param['type'] == 'boolean':
                example_value = "true"
            else:
                example_value = f"example_{param_name}"
            example_parts.append(f"<{param_name}>{example_value}</{param_name}>")
        example_parts.append(f"</{tool_name}>")
        
        return "\n".join(example_parts)

    # This helper parses a single, isolated XML block assuming it's a complete tool call
    def parse_isolated_xml_block(xml_block: str, identified_tool_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]: # Updated return type
        tool_args = {}
        error_message: Optional[str] = None
        
        # Enhanced XML cleaning
        cleaned_xml_block = _sanitize_xml_block(xml_block, identified_tool_name)
        
        try:
            root = ET.fromstring(cleaned_xml_block)
            if root.tag.lower() != identified_tool_name.lower():
                error_message = f"XML root tag '{root.tag}' does not match expected tool name '{identified_tool_name}'. Expected: <{identified_tool_name}>...</{identified_tool_name}>"
                return None, error_message

            for child in root:
                param_name = child.tag
                param_value = child.text.strip() if child.text else ""
                tool_args[param_name] = html.unescape(param_value)
            return tool_args, None # Success
            
        except ET.ParseError as e:
            # Generate detailed error message with correction guidance
            error_details = str(e)
            corrected_example = _generate_corrected_xml_example(identified_tool_name, tools)
            
            error_message = f"XML ParseError: {error_details}. "
            
            if "junk after document element" in error_details:
                error_message += "This usually means there's extra content after the closing tag. "
            elif "mismatched tag" in error_details:
                error_message += "This means opening and closing tags don't match. "
            elif "not well-formed" in error_details:
                error_message += "The XML structure is malformed. "
            
            error_message += f"Correct format:\n{corrected_example}"
            
            logger.error(f"[PARSE_HELPER] Enhanced error for tool '{identified_tool_name}': {error_message}")
            logger.debug(f"[PARSE_HELPER] Original block: '{xml_block[:200]}...'")
            logger.debug(f"[PARSE_HELPER] Cleaned block: '{cleaned_xml_block[:200]}...'")
            
            return None, error_message
            
        except Exception as e:
            error_message = f"Unexpected error parsing XML: {str(e)}. Please ensure your XML follows the correct format: <{identified_tool_name}>...</{identified_tool_name}>"
            logger.error(f"[PARSE_HELPER] Unexpected error for tool '{identified_tool_name}': {error_message}")
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
            logger.debug(f"[PARSE_DEBUG] Skipping overlapping match at span {match_span} for candidate '{tool_name_candidate}'.")
            continue

        actual_tool_name = next((name for name in tools if name.lower() == tool_name_candidate), None)
        if not actual_tool_name:
            logger.warning(f"[PARSE_DEBUG] Agent {agent_id}: Regex matched candidate <{tool_name_candidate}> but no such tool is registered. Skipping.")
            # Optionally, this could also be a parsing error if strictness is desired
            # For now, just skipping as it's not a malformed call for a *known* tool
            continue

        logger.info(f"Agent {agent_id}: Detected call for tool '{actual_tool_name}' (candidate: '{tool_name_candidate}') at span {match_span} (Markdown: {is_markdown})")
        
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
                logger.warning(f"Agent {agent_id}: Tool '{actual_tool_name}' call MISSING required parameter(s) defined in schema: {missing_required_params}. Tool execution will likely fail if these are truly needed by the tool's logic.")

            found_calls_details.append((actual_tool_name, parsed_args, match_span))
            processed_spans.add(match_span)
        else:
            # Error occurred in parse_isolated_xml_block
            logger.warning(f"Agent {agent_id}: Failed to parse XML block for tool '{actual_tool_name}' at span {match_span}. Error: {error_detail}")
            parsing_errors.append({
                "tool_name": actual_tool_name,
                "error_message": error_detail or "Unknown parsing error from parse_isolated_xml_block",
                "xml_block": xml_block_to_parse, # The original block attempted
                "is_markdown": is_markdown,
                "span": match_span
            })
            # Note: We might still want to add the span to processed_spans if we decide one error per block is enough
            # For now, if a block errors, it won't be added to processed_spans, meaning other interpretations of it (if any) could be tried.
            # However, given the greedy nature of the regex, this is unlikely for typical tool calls.
            # Consider adding to processed_spans here if we want to ensure an erroneous block isn't re-processed by a less specific regex:
            processed_spans.add(match_span)


    if not found_calls_details and not parsing_errors:
        logger.debug(f"Agent {agent_id}: [PARSE_DEBUG] No valid XML tool calls or parsing errors found in buffer after full processing.")
    elif found_calls_details:
        logger.info(f"Agent {agent_id}: Found {len(found_calls_details)} valid XML tool call(s) in buffer.")
    if parsing_errors:
         logger.info(f"Agent {agent_id}: Encountered {len(parsing_errors)} XML parsing error(s) in buffer.")

    return {"valid_calls": found_calls_details, "parsing_errors": parsing_errors}
# END OF FILE src/agents/agent_tool_parser.py
