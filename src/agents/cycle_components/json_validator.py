import json
import re
import logging
from typing import List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

class JSONValidator:
    """Handles JSON validation and recovery for agent tool calls."""
    
    def __init__(self):
        """Initialize the JSON validator."""
        pass
        
    def validate_json(self, json_content: str) -> dict:
        """
        Validate JSON content and return detailed validation result.
        
        Args:
            json_content: The JSON string to validate
            
        Returns:
            Dict with keys: is_valid, error_message, suggestions
        """
        if not json_content or not json_content.strip():
            return {
                'is_valid': False, 
                'error_message': "Empty JSON content",
                'suggestions': ["Provide non-empty JSON content"]
            }
            
        try:
            json.loads(json_content)
            return {'is_valid': True, 'error_message': None, 'suggestions': []}
        except json.JSONDecodeError as e:
            suggestions = self._generate_json_suggestions(json_content, str(e))
            return {
                'is_valid': False, 
                'error_message': str(e),
                'suggestions': suggestions
            }
            
    def _generate_json_suggestions(self, json_content: str, error: str) -> List[str]:
        suggestions = []
        if "Expecting property name enclosed in double quotes" in error:
            suggestions.append("Ensure all JSON keys and string values are enclosed in double quotes (\"), not single quotes (').")
        elif "Expecting ',' delimiter" in error or "Expecting value" in error:
            suggestions.append("Check for missing commas between array elements or object properties.")
        elif "Unterminated string starting at" in error:
            suggestions.append("Check for unescaped quotes inside strings, or missing closing quotes.")
        return suggestions

    def recover_json(self, json_content: str) -> dict:
        """
        Attempt to recover malformed JSON using common patterns.
        """
        if not json_content:
            return {
                'success': False,
                'recovered_json': json_content,
                'was_modified': False,
                'error': 'Empty JSON content',
                'applied_fixes': []
            }
            
        original_content = json_content
        applied_fixes = []
        
        # Step 1: Remove markdown code fences
        if re.search(r'```[^`]*?\{.*?}[^`]*?```', json_content, re.DOTALL):
            fence_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
            fence_matches = re.findall(fence_pattern, json_content, re.DOTALL)
            if fence_matches:
                json_content = fence_matches[0].strip()
                applied_fixes.append("Removed markdown code fences")
                logger.debug("Removed markdown code fences from JSON")

        # Step 2: Try replacing single quotes with double quotes (very basic heuristic)
        try:
            json.loads(json_content)
        except json.JSONDecodeError:
            potential_json = json_content.replace("'", '"')
            try:
                json.loads(potential_json)
                json_content = potential_json
                applied_fixes.append("Replaced single quotes with double quotes")
            except json.JSONDecodeError:
                pass
                
        # Validate the recovered JSON
        validation_result = self.validate_json(json_content)
        
        was_modified = len(applied_fixes) > 0
        success = validation_result['is_valid']
        
        return {
            'success': success,
            'recovered_json': json_content,
            'was_modified': was_modified,
            'error': validation_result['error_message'] if not success else None,
            'applied_fixes': applied_fixes,
            'suggestions': validation_result.get('suggestions', [])
        }
        
    def extract_tool_calls(self, text: str) -> List[dict]:
        """
        Extract and validate tool calls from agent response text.
        """
        tool_calls = []
        
        fence_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        fence_matches = re.findall(fence_pattern, text, re.DOTALL)
        
        blocks_to_test = fence_matches if fence_matches else re.findall(r'(\{[\s\S]*?\})', text)
        
        for block in blocks_to_test:
            block = block.strip()
            recovery_result = self.recover_json(block)
            if recovery_result['success']:
                try:
                    parsed_json = json.loads(recovery_result['recovered_json'])
                    if isinstance(parsed_json, dict) and "action" in parsed_json:
                        tool_calls.append({
                            'tool': parsed_json.get('action'),
                            'content': parsed_json,
                            'json': recovery_result['recovered_json'],
                            'recovered': recovery_result['was_modified']
                        })
                except json.JSONDecodeError:
                    continue
                    
        return tool_calls

json_validator = JSONValidator()
