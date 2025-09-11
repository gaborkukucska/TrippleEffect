# START OF FILE src/agents/cycle_components/xml_validator.py
"""
XML Validation and Recovery Module for TrippleEffect Framework

This module provides XML validation and recovery mechanisms to handle malformed XML
from agent responses, particularly for tool calls and state changes.
"""

import re
import logging
from typing import Optional, Tuple, List
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

class XMLValidator:
    """Handles XML validation and recovery for agent tool calls."""
    
    def __init__(self):
        """Initialize the XML validator with common recovery patterns."""
        # Common XML tool patterns used in the framework
        self.tool_patterns = [
            'request_state',
            'think',
            'plan', 
            'title',
            'tool_information',
            'manage_team',
            'project_management',
            'send_message',
            'file_system',
            'web_search',
            'knowledge_base',
            'github_tool',
            'system_help'
        ]
        
        # Recovery patterns for common XML issues
        self.recovery_patterns = [
            # Missing closing tags
            (r'<(\w+)>([^<]*?)(?!</\1>)(?=<|\Z)', r'<\1>\2</\1>'),
            # Unclosed self-closing tags
            (r'<(\w+)\s+([^>]*?)(?<!/)>', r'<\1 \2/>'),
            # Malformed attributes (missing quotes)
            (r'<(\w+)\s+(\w+)=([^"\s>]+)([^>]*?)>', r'<\1 \2="\3"\4>'),
            # Fix nested tags without proper closing
            (r'<(\w+)><(\w+)>([^<]*?)(?!</\2>)(?=<|\Z)', r'<\1><\2>\3</\2></\1>'),
        ]
    
    def validate_xml(self, xml_content: str) -> dict:
        """
        Validate XML content and return detailed validation result.
        
        Args:
            xml_content: The XML string to validate
            
        Returns:
            Dict with keys: is_valid, error_message, suggestions
        """
        if not xml_content or not xml_content.strip():
            return {
                'is_valid': False, 
                'error_message': "Empty XML content",
                'suggestions': ["Provide non-empty XML content"]
            }
            
        try:
            # Try to parse as XML
            ET.fromstring(f"<root>{xml_content}</root>")
            return {'is_valid': True, 'error_message': None, 'suggestions': []}
        except ET.ParseError as e:
            suggestions = self._generate_xml_suggestions(xml_content, str(e))
            return {
                'is_valid': False, 
                'error_message': str(e),
                'suggestions': suggestions
            }
    
    def _generate_xml_suggestions(self, xml_content: str, error: str) -> List[str]:
        """Generate helpful suggestions based on XML parsing error."""
        suggestions = []
        
        # Check for common patterns in the malformed XML
        if "not well-formed" in error.lower():
            # Look for common malformation patterns
            if re.search(r'```[^`]*?<\w+>[^<]*<[^>]*>[^<]*</[^>]*>[^<]*</\w+>[^`]*?```', xml_content):
                suggestions.append("Remove markdown code fences (```) around XML tool calls")
            
            if re.search(r'<\w+>[^<]*</', xml_content):
                suggestions.append("Ensure all XML tags are properly opened with '<' and closed with '/>'")
                
            if re.search(r'&(?!amp;|lt;|gt;|quot;|apos;)', xml_content):
                suggestions.append("Escape special characters: & as &amp;, < as &lt;, > as &gt;")
        
        if "mismatched tag" in error.lower():
            suggestions.append("Check that opening and closing tag names match exactly")
            
        return suggestions
    
    def recover_xml(self, xml_content: str) -> dict:
        """
        Attempt to recover malformed XML using common patterns.
        
        Args:
            xml_content: The potentially malformed XML string
            
        Returns:
            Dict with keys: success, recovered_xml, was_modified, error, applied_fixes
        """
        if not xml_content:
            return {
                'success': False,
                'recovered_xml': xml_content,
                'was_modified': False,
                'error': 'Empty XML content',
                'applied_fixes': []
            }
            
        original_content = xml_content
        applied_fixes = []
        
        # Step 1: Remove markdown code fences - this is the main issue from logs
        if re.search(r'```[^`]*?<.*?>[^`]*?```', xml_content, re.DOTALL):
            # Extract XML from markdown code fences
            fence_pattern = r'```(?:\w+)?\s*(.*?)\s*```'
            fence_matches = re.findall(fence_pattern, xml_content, re.DOTALL)
            if fence_matches:
                xml_content = fence_matches[0].strip()
                applied_fixes.append("Removed markdown code fences")
                logger.debug("Removed markdown code fences from XML")
        
        # Step 2: Fix the specific tool_information malformed pattern from logs
        # Pattern: <tool_information><action>execute</action><tool_name>OTHER_TOOL</tool_name><parameters>action=list_tools</parameters></tool_information>
        malformed_tool_info_pattern = r'<tool_information>\s*<action>execute</action>\s*<tool_name>([^<]+)</tool_name>\s*<parameters>([^<]+)</parameters>\s*</tool_information>'
        match = re.search(malformed_tool_info_pattern, xml_content)
        if match:
            target_tool = match.group(1)
            parameters = match.group(2)
            
            # Parse the parameters to extract the actual action
            if 'action=' in parameters:
                # Extract action value from parameters like "action=list_tools"
                action_match = re.search(r'action=([^,\s]+)', parameters)
                if action_match:
                    actual_action = action_match.group(1)
                    
                    # Create the correct tool_information call
                    if actual_action in ['list_tools', 'get_info']:
                        if actual_action == 'list_tools':
                            xml_content = '<tool_information><action>list_tools</action></tool_information>'
                        else:  # get_info
                            xml_content = f'<tool_information><action>get_info</action><tool_name>{target_tool}</tool_name></tool_information>'
                        
                        applied_fixes.append(f"Fixed malformed tool_information pattern - converted execute/{target_tool} to proper {actual_action}")
                    else:
                        # If they want to use the actual tool, construct that call
                        xml_content = f'<{target_tool}><action>{actual_action}</action></{target_tool}>'
                        applied_fixes.append(f"Converted malformed tool_information to direct {target_tool} call")
        
        # Step 3: Fix malformed opening brackets (the specific issue from logs)
        # Pattern: tool_information><action>... should be <tool_information><action>...
        malformed_bracket_pattern = r'(\w+)><(\w+)>'
        if re.search(malformed_bracket_pattern, xml_content):
            xml_content = re.sub(malformed_bracket_pattern, r'<\1><\2>', xml_content)
            applied_fixes.append("Fixed malformed opening brackets")
            logger.debug("Fixed malformed opening brackets in XML")
        
        # Step 3: Apply general recovery patterns
        for pattern, replacement in self.recovery_patterns:
            new_content = re.sub(pattern, replacement, xml_content, flags=re.IGNORECASE | re.MULTILINE)
            if new_content != xml_content:
                xml_content = new_content
                applied_fixes.append(f"Applied pattern: {pattern[:30]}...")
                logger.debug(f"Applied XML recovery pattern: {pattern}")
        
        # Step 4: Special recovery for tool calls that are cut off mid-tag
        xml_content, cut_recovery = self._recover_truncated_xml(xml_content)
        if cut_recovery:
            applied_fixes.append("Fixed truncated XML tags")
        
        # Step 5: Validate the recovered XML
        validation_result = self.validate_xml(xml_content)
        if not validation_result['is_valid']:
            # Try more aggressive recovery
            xml_content, aggressive_recovery = self._aggressive_xml_recovery(xml_content)
            if aggressive_recovery:
                applied_fixes.append("Applied aggressive recovery")
                validation_result = self.validate_xml(xml_content)
        
        was_modified = len(applied_fixes) > 0
        success = validation_result['is_valid']
        
        if was_modified:
            logger.info(f"XML recovery applied. Valid after recovery: {success}. Fixes: {applied_fixes}")
            logger.debug(f"Original: {original_content[:200]}...")
            logger.debug(f"Recovered: {xml_content[:200]}...")
        
        return {
            'success': success,
            'recovered_xml': xml_content,
            'was_modified': was_modified,
            'error': validation_result['error_message'] if not success else None,
            'applied_fixes': applied_fixes,
            'suggestions': validation_result.get('suggestions', [])
        }
    
    def _recover_truncated_xml(self, xml_content: str) -> Tuple[str, bool]:
        """Recover XML that was truncated mid-tag."""
        modified = False
        
        # Handle tags that are opened but not closed at end of content
        for tool in self.tool_patterns:
            # Look for opening tag without corresponding closing tag
            pattern = f'<{tool}[^>]*>'
            if re.search(pattern, xml_content, re.IGNORECASE):
                closing_pattern = f'</{tool}>'
                if not re.search(closing_pattern, xml_content, re.IGNORECASE):
                    # Add closing tag at the end
                    xml_content += f'</{tool}>'
                    modified = True
                    logger.debug(f"Added missing closing tag for {tool}")
        
        return xml_content, modified
    
    def _aggressive_xml_recovery(self, xml_content: str) -> Tuple[str, bool]:
        """Apply more aggressive recovery techniques."""
        modified = False
        
        # Remove obviously broken fragments at the end
        lines = xml_content.split('\n')
        clean_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Skip lines that look like partial/broken tags
            if re.match(r'^<[^>]*$', line) or re.match(r'^[^<]*>$', line):
                logger.debug(f"Removing broken XML fragment: {line}")
                modified = True
                continue
                
            clean_lines.append(line)
        
        if modified:
            xml_content = '\n'.join(clean_lines)
        
        return xml_content, modified
    
    def extract_tool_calls(self, text: str) -> List[dict]:
        """
        Extract and validate tool calls from agent response text.
        
        Args:
            text: The agent's response text containing potential XML tool calls
            
        Returns:
            List of validated tool call dictionaries
        """
        tool_calls = []
        
        # First try to recover any malformed XML
        recovery_result = self.recover_xml(text)
        recovered_text = recovery_result['recovered_xml']
        
        if recovery_result['was_modified']:
            logger.info(f"XML recovery was applied to agent response. Fixes: {recovery_result['applied_fixes']}")
        
        # Extract tool calls using regex patterns
        for tool in self.tool_patterns:
            pattern = f'<{tool}[^>]*?>(.*?)</{tool}>'
            matches = re.findall(pattern, recovered_text, re.DOTALL | re.IGNORECASE)
            
            for match in matches:
                # Validate the complete tool call XML
                tool_xml = f'<{tool}>{match}</{tool}>'
                validation_result = self.validate_xml(tool_xml)
                
                if validation_result['is_valid']:
                    tool_calls.append({
                        'tool': tool,
                        'content': match.strip(),
                        'xml': tool_xml
                    })
                else:
                    logger.warning(f"Invalid {tool} tool call found: {validation_result['error_message']}")
                    # Try to recover this specific tool call
                    tool_recovery_result = self.recover_xml(tool_xml)
                    if tool_recovery_result['success']:
                        logger.info(f"Successfully recovered {tool} tool call")
                        tool_calls.append({
                            'tool': tool,
                            'content': match.strip(),
                            'xml': tool_recovery_result['recovered_xml'],
                            'recovered': True
                        })
        
        return tool_calls

# Global instance for framework use
xml_validator = XMLValidator()
