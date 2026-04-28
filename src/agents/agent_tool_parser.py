# START OF FILE src/agents/agent_tool_parser.py
import re
import json as json_module  # Renamed to avoid shadowing
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

# Regex for <tool_call>{"name": "...", "arguments": {...}}</tool_call> format (qwen3, etc.)
TOOL_CALL_JSON_PATTERN = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL
)


def _parse_tool_call_json_blocks(
    text_buffer: str,
    tools: Dict[str, BaseTool],
    processed_spans: set,
    agent_id: str
) -> Tuple[List[ValidCallTuple], List[ParsingErrorDict]]:
    """
    Parse <tool_call>{"name": "tool_name", "arguments": {...}}</tool_call> format.
    This is used by qwen3 and other models that wrap JSON tool calls in <tool_call> tags.
    
    Returns:
        Tuple of (valid_calls, parsing_errors)
    """
    found_calls: List[ValidCallTuple] = []
    errors: List[ParsingErrorDict] = []

    for match in TOOL_CALL_JSON_PATTERN.finditer(text_buffer):
        span = match.span()

        # Check for overlap with already-processed spans
        overlapping = False
        for proc_start, proc_end in processed_spans:
            if max(span[0], proc_start) < min(span[1], proc_end):
                overlapping = True
                break
        if overlapping:
            continue

        json_str = match.group(1).strip()
        try:
            call_data = json_module.loads(json_str)
        except json_module.JSONDecodeError as e:
            logger.warning(f"Agent {agent_id}: <tool_call> JSON parse error: {e}. Raw: '{json_str[:200]}'")
            errors.append({
                "tool_name": "unknown",
                "error_message": f"JSON parse error in <tool_call> block: {e}",
                "xml_block": match.group(0),
                "is_markdown": False,
                "span": span
            })
            processed_spans.add(span)
            continue

        if not isinstance(call_data, dict):
            logger.warning(f"Agent {agent_id}: <tool_call> JSON is not a dict: {type(call_data)}")
            processed_spans.add(span)
            continue

        tool_name_from_json = call_data.get("name", "")
        tool_args = call_data.get("arguments", {})

        if not tool_name_from_json:
            logger.warning(f"Agent {agent_id}: <tool_call> JSON missing 'name' field.")
            available_tools = ", ".join(f"'{name}'" for name in tools.keys())
            errors.append({
                "tool_name": "unknown_json_tool",
                "error_message": f"You output an empty <tool_call> block with no 'name'. You MUST specify the 'name' of the tool you intend to use. Available tools are: {available_tools}.",
                "xml_block": match.group(0),
                "is_markdown": False,
                "span": span
            })
            processed_spans.add(span)
            continue

        # Resolve tool name (case-insensitive match against registered tools)
        actual_tool_name = next(
            (name for name in tools if name.lower() == tool_name_from_json.lower()),
            None
        )

        if not actual_tool_name:
            logger.warning(
                f"Agent {agent_id}: <tool_call> references tool '{tool_name_from_json}' "
                f"which is not registered. Skipping."
            )
            processed_spans.add(span)
            continue

        # Ensure arguments is a dict
        if not isinstance(tool_args, dict):
            logger.warning(
                f"Agent {agent_id}: <tool_call> 'arguments' for '{actual_tool_name}' "
                f"is not a dict (got {type(tool_args).__name__}). Wrapping."
            )
            tool_args = {"value": str(tool_args)}

        logger.info(
            f"Agent {agent_id}: Parsed <tool_call> JSON format for tool "
            f"'{actual_tool_name}' at span {span}. Args: {list(tool_args.keys())}"
        )

        found_calls.append((actual_tool_name, tool_args, span))
        processed_spans.add(span)

    return found_calls, errors


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
    Also supports <tool_call>{"name": "...", "arguments": {...}}</tool_call> JSON format.
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
        
        # Escape content inside known parameter tags to prevent ET.ParseError on unescaped HTML/XML
        # IMPORTANT: Also escape content in ALIAS tags that LLMs commonly use instead of schema names
        PARAM_ALIAS_MAP = {
            "code_editor": {
                "chunks": ["replacements", "replace_chunks", "edits", "replacements_json", "modifications"],
                "filename": ["filepath", "file_path", "file_name", "path", "file"],
            },
            "file_system": {
                "search_block": ["search", "search_string", "find", "find_text", "search_text", "search_term"],
                "replace_block": ["replace", "replacement", "replace_string", "replace_text", "replace_term"],
                "filename": ["filepath", "file_path", "file_name", "path", "file"],
                "file_content": ["content", "text", "data", "body"],
                "new_content": ["content", "text", "new_text"],
            },
            "send_message": {
                "message_content": ["content", "message", "text"],
                "target_agent_id": ["target", "agent", "recipient", "to"],
            }
        }
        
        # Build a complete list of tag names whose content should be escaped
        all_escapable_tags: set = set()
        if tool_name in tools:
            tool_schema = tools[tool_name].get_schema()
            params = tool_schema.get('parameters', [])
            param_names = [p['name'] for p in params]
            all_escapable_tags.update(param_names)
            
            # Add aliases from the map
            tool_aliases = PARAM_ALIAS_MAP.get(tool_name, {})
            for canonical, aliases in tool_aliases.items():
                all_escapable_tags.add(canonical)
                all_escapable_tags.update(aliases)
        else:
            param_names = []
        
        for tag_name in all_escapable_tags:
            def _escape_raw_content(raw_text: str, p_name: str = tag_name) -> str:
                """Escape raw content for safe XML embedding."""
                raw = html.unescape(raw_text)
                if raw.strip().startswith("<![CDATA[") and raw.strip().endswith("]]>"):
                    raw = raw.strip()[9:-3]
                safe = raw.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                return f"<{p_name}>{safe}</{p_name}>"

            # Try standard match first: <param>...</param>
            pattern = re.compile(rf"<{tag_name}>(.*?)</{tag_name}>", flags=re.IGNORECASE | re.DOTALL)
            if pattern.search(cleaned):
                cleaned = pattern.sub(lambda m: _escape_raw_content(m.group(1)), cleaned)
            else:
                # Fallback: handle missing closing tag (e.g., <content>HTML...</tool_name>)
                open_tag_pattern = re.compile(rf"<{tag_name}>(.*)", flags=re.IGNORECASE | re.DOTALL)
                open_match = open_tag_pattern.search(cleaned)
                if open_match:
                    content_after_open = open_match.group(1)
                    end_marker = f"</{tool_name}>"
                    end_pos = content_after_open.rfind(end_marker)
                    if end_pos == -1:
                        end_pos = content_after_open.lower().rfind(f"</{tool_name.lower()}>")
                    
                    if end_pos != -1:
                        earliest_sibling = end_pos
                        all_known_tags = all_escapable_tags | set(param_names)
                        for other_tag in all_known_tags:
                            if other_tag == tag_name:
                                continue
                            sibling_pos = content_after_open.find(f"<{other_tag}>")
                            if sibling_pos == -1:
                                sibling_pos = content_after_open.lower().find(f"<{other_tag.lower()}>")
                            if sibling_pos != -1 and sibling_pos < earliest_sibling:
                                earliest_sibling = sibling_pos
                        
                        actual_content = content_after_open[:earliest_sibling]
                        remainder = content_after_open[earliest_sibling:]
                        
                        escaped_section = _escape_raw_content(actual_content)
                        rebuilt = cleaned[:open_match.start()] + escaped_section + remainder
                        cleaned = rebuilt
                        logger.info(f"[SANITIZE] Fixed missing </{tag_name}> tag for tool '{tool_name}'. Extracted and escaped {len(actual_content)} chars of content.")

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

            # Extract attributes first
            for key, val in root.attrib.items():
                tool_args[key] = html.unescape(val)

            # Extract child elements (they override attributes if same name)
            for child in root:
                param_name = child.tag
                param_value = child.text.strip() if child.text else ""
                tool_args[param_name] = html.unescape(param_value)
                
            # Fallback: if there's raw text inside the root element and we didn't parse it as a child tag
            if root.text and root.text.strip():
                raw_text = html.unescape(root.text.strip())
                # Try to map to known primary content parameters if they are missing
                if identified_tool_name.lower() == "file_system" and "content" not in tool_args:
                    tool_args["content"] = raw_text
                elif identified_tool_name.lower() == "send_message" and "message_content" not in tool_args:
                    tool_args["message_content"] = raw_text
                elif identified_tool_name.lower() == "code_editor" and "chunks" not in tool_args and "replacements" not in tool_args:
                    tool_args["chunks"] = raw_text
                elif identified_tool_name.lower() == "project_management" and "task_description" not in tool_args:
                    tool_args["task_description"] = raw_text
                elif "content" not in tool_args:
                    tool_args["content"] = raw_text
                    
            return tool_args, None # Success
            
        except ET.ParseError as e:
            # --- Heuristic Recovery for send_message ---
            if identified_tool_name.lower() == "send_message":
                target_match = re.search(r'<target_agent_id[^>]*>(.*?)</target_agent_id>', xml_block, re.IGNORECASE | re.DOTALL)
                if not target_match:
                    # check aliases
                    for alias in ["target", "agent", "recipient", "to"]:
                        target_match = re.search(rf'<{alias}[^>]*>(.*?)</{alias}>', xml_block, re.IGNORECASE | re.DOTALL)
                        if target_match: break
                target_id = target_match.group(1).strip() if target_match else ""
                
                content_match = re.search(r'<message_content[^>]*>(.*?)</message_content>', xml_block, re.IGNORECASE | re.DOTALL)
                if not content_match:
                    # check aliases
                    for alias in ["content", "message", "text"]:
                        content_match = re.search(rf'<{alias}[^>]*>(.*?)</{alias}>', xml_block, re.IGNORECASE | re.DOTALL)
                        if content_match: break
                        
                if not content_match:
                    # If closing tag is missing completely
                    open_tag = re.search(r'<message_content[^>]*>(.*)', xml_block, re.IGNORECASE | re.DOTALL)
                    if not open_tag:
                        for alias in ["content", "message", "text"]:
                            open_tag = re.search(rf'<{alias}[^>]*>(.*)', xml_block, re.IGNORECASE | re.DOTALL)
                            if open_tag: break
                    if open_tag:
                        content_str = open_tag.group(1).strip()
                        end_idx = content_str.lower().rfind(f"</{identified_tool_name.lower()}>")
                        if end_idx != -1:
                            content_str = content_str[:end_idx].strip()
                        message_content = content_str
                    else:
                        message_content = ""
                else:
                    message_content = content_match.group(1).strip()
                    
                if target_id and message_content:
                    logger.info(f"[PARSE_HELPER] Successfully salvaged malformed 'send_message' call using heuristic fallback.")
                    return {"target_agent_id": target_id, "message_content": message_content}, None
            # -------------------------------------------

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

    # 3. Fallback: Parse <tool_call>{"name": "...", "arguments": {...}}</tool_call> JSON format
    # This handles models like qwen3 that use JSON-in-XML format instead of pure XML
    if not found_calls_details and not parsing_errors:
        json_calls, json_errors = _parse_tool_call_json_blocks(
            text_buffer, tools, processed_spans, agent_id
        )
        if json_calls:
            found_calls_details.extend(json_calls)
            logger.info(f"Agent {agent_id}: Found {len(json_calls)} valid <tool_call> JSON format call(s) in buffer.")
        if json_errors:
            parsing_errors.extend(json_errors)

    if not found_calls_details and not parsing_errors:
        logger.debug(f"Agent {agent_id}: [PARSE_DEBUG] No valid XML tool calls or parsing errors found in buffer after full processing.")
    elif found_calls_details:
        logger.info(f"Agent {agent_id}: Found {len(found_calls_details)} valid tool call(s) in buffer (XML + JSON formats).")
    if parsing_errors:
         logger.info(f"Agent {agent_id}: Encountered {len(parsing_errors)} tool call parsing error(s) in buffer.")

    return {"valid_calls": found_calls_details, "parsing_errors": parsing_errors}
# END OF FILE src/agents/agent_tool_parser.py
