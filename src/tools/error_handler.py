# START OF FILE src/tools/error_handler.py
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from difflib import get_close_matches
from enum import Enum

logger = logging.getLogger(__name__)

class ErrorType(Enum):
    """Categories of tool execution errors"""
    INVALID_ACTION = "invalid_action"
    MISSING_PARAMETER = "missing_parameter" 
    INVALID_PARAMETER = "invalid_parameter"
    AUTHORIZATION_ERROR = "authorization_error"
    EXECUTION_ERROR = "execution_error"
    FORMAT_ERROR = "format_error"
    TOOL_NOT_FOUND = "tool_not_found"

class ToolErrorHandler:
    """
    Centralized error handling system for tools with enhanced feedback,
    context-aware suggestions, and learning capabilities.
    """
    
    def __init__(self):
        self.error_patterns = {}  # Track common error patterns
        self.success_patterns = {}  # Track successful corrections
        
        # Common action corrections across tools
        self.global_action_corrections = {
            'search': ['search_knowledge', 'search_agent_thoughts'],
            'save': ['save_knowledge', 'write', 'save_file'],
            'store': ['save_knowledge', 'write'],
            'find': ['search_knowledge', 'search', 'list'],
            'lookup': ['search_knowledge', 'get'],
            'retrieve': ['search_knowledge', 'read'],
            'get': ['search_knowledge', 'read', 'get_info'],
            'create': ['write', 'create_agent', 'create_team', 'mkdir'],
            'make': ['mkdir', 'create_agent', 'create_team'],
            'list': ['list_tools', 'list_agents', 'list_tasks'],
            'show': ['list_tools', 'list_agents', 'read'],
        }
        
    def generate_enhanced_error_response(
        self,
        error_type: ErrorType,
        tool_name: str,
        attempted_action: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Dict[str, Any]] = None,
        available_actions: Optional[List[str]] = None,
        tool_schema: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate enhanced error response with context-aware suggestions
        and actionable guidance.
        """
        
        # Base error response structure
        error_response = {
            "status": "error",
            "error_type": error_type.value,
            "tool_name": tool_name,
            "message": "",
            "suggestions": [],
            "corrected_examples": [],
            "alternative_tools": [],
            "context_help": "",
            "execution_id": f"err_{tool_name}_{int(time.time())}",
            "learning_data": {
                "pattern": f"{tool_name}_{error_type.value}_{attempted_action or 'unknown'}",
                "agent_id": agent_context.get("agent_id") if agent_context else None,
                "timestamp": time.time()
            }
        }
        
        # Generate specific error handling based on type
        if error_type == ErrorType.INVALID_ACTION:
            return self._handle_invalid_action_error(
                error_response, tool_name, attempted_action, 
                available_actions, agent_context, tool_schema
            )
        elif error_type == ErrorType.MISSING_PARAMETER:
            return self._handle_missing_parameter_error(
                error_response, tool_name, tool_args, tool_schema
            )
        elif error_type == ErrorType.INVALID_PARAMETER:
            return self._handle_invalid_parameter_error(
                error_response, tool_name, tool_args, tool_schema
            )
        elif error_type == ErrorType.TOOL_NOT_FOUND:
            return self._handle_tool_not_found_error(
                error_response, tool_name, agent_context
            )
        else:
            # Generic error handling
            error_response["message"] = f"Error using tool '{tool_name}': {error_type.value}"
            error_response["suggestions"] = [
                f"Check the tool's documentation using: <tool_information><action>get_info</action><tool_name>{tool_name}</tool_name></tool_information>",
                "Verify all required parameters are provided",
                "Ensure proper XML formatting"
            ]
            
        return error_response
    
    def _handle_invalid_action_error(
        self, 
        error_response: Dict[str, Any], 
        tool_name: str, 
        attempted_action: Optional[str],
        available_actions: Optional[List[str]],
        agent_context: Optional[Dict[str, Any]],
        tool_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Handle invalid action errors with smart suggestions"""
        
        if not attempted_action:
            error_response["message"] = f"Missing 'action' parameter for tool '{tool_name}'"
            error_response["suggestions"] = [
                f"All tools require an 'action' parameter",
                f"Get available actions: <tool_information><action>get_info</action><tool_name>{tool_name}</tool_name></tool_information>"
            ]
            return error_response
            
        # Find close matches for the attempted action
        suggestions = []
        corrected_examples = []
        
        if available_actions:
            # Use fuzzy matching to find similar actions
            close_matches = get_close_matches(
                attempted_action, available_actions, n=3, cutoff=0.6
            )
            
            if close_matches:
                primary_suggestion = close_matches[0]
                error_response["message"] = (
                    f"Invalid action '{attempted_action}' for tool '{tool_name}'. "
                    f"Did you mean '{primary_suggestion}'? "
                    f"Valid actions: {', '.join(available_actions)}"
                )
                
                # Generate corrected XML example
                corrected_examples.append(
                    f"<{tool_name}><action>{primary_suggestion}</action></{tool_name}>"
                )
                
                suggestions.extend([
                    f"Try using '{primary_suggestion}' instead of '{attempted_action}'",
                    f"Valid actions for '{tool_name}': {', '.join(available_actions[:3])}{'...' if len(available_actions) > 3 else ''}"
                ])
            else:
                error_response["message"] = (
                    f"Invalid action '{attempted_action}' for tool '{tool_name}'. "
                    f"Valid actions: {', '.join(available_actions)}"
                )
                
        # Check global action corrections
        if attempted_action.lower() in self.global_action_corrections:
            global_suggestions = self.global_action_corrections[attempted_action.lower()]
            # Filter suggestions that might be valid for this tool
            relevant_suggestions = [s for s in global_suggestions if not available_actions or s in available_actions]
            
            if relevant_suggestions:
                suggestions.append(f"For '{attempted_action}', you might want: {', '.join(relevant_suggestions[:2])}")
                
        # Add context-aware suggestions
        if agent_context:
            agent_type = agent_context.get("agent_type")
            agent_state = agent_context.get("agent_state")
            
            if agent_type == "admin" and agent_state == "work":
                suggestions.append("In work state, focus on information-gathering tools like 'list_tools', 'search_knowledge'")
            elif tool_name == "knowledge_base" and attempted_action in ["learn", "remember"]:
                suggestions.insert(0, "For saving information, use 'save_knowledge' action")
                corrected_examples.insert(0, 
                    f"<knowledge_base><action>save_knowledge</action><summary>Your learning here</summary><keywords>relevant,keywords</keywords></knowledge_base>"
                )
                
        error_response["suggestions"] = suggestions
        error_response["corrected_examples"] = corrected_examples
        
        return error_response
    
    def _handle_missing_parameter_error(
        self,
        error_response: Dict[str, Any],
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        tool_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Handle missing parameter errors"""
        
        missing_params = []
        provided_params = list(tool_args.keys()) if tool_args else []
        
        if tool_schema and tool_schema.get('parameters'):
            for param in tool_schema['parameters']:
                if param.get('required', True) and param['name'] not in provided_params:
                    missing_params.append(param['name'])
                    
        error_response["message"] = (
            f"Tool '{tool_name}' missing required parameters: {', '.join(missing_params)}"
        )
        
        suggestions = [
            f"Add missing parameters: {', '.join(missing_params)}",
            f"Get detailed usage: <tool_information><action>get_info</action><tool_name>{tool_name}</tool_name></tool_information>"
        ]
        
        # Generate example with missing parameters
        if tool_schema and missing_params:
            example_parts = [f"<{tool_name}>"]
            if tool_args and 'action' in tool_args:
                example_parts.append(f"<action>{tool_args['action']}</action>")
            
            for param in missing_params[:2]:  # Show first 2 missing params
                example_parts.append(f"<{param}>your_value_here</{param}>")
                
            example_parts.append(f"</{tool_name}>")
            error_response["corrected_examples"] = ["\n".join(example_parts)]
            
        error_response["suggestions"] = suggestions
        return error_response
    
    def _handle_invalid_parameter_error(
        self,
        error_response: Dict[str, Any],
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        tool_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Handle invalid parameter format/type errors"""
        
        error_response["message"] = f"Invalid parameter format for tool '{tool_name}'"
        error_response["suggestions"] = [
            "Check parameter types and formats",
            "Ensure string parameters don't contain special characters",
            f"Get parameter details: <tool_information><action>get_info</action><tool_name>{tool_name}</tool_name></tool_information>"
        ]
        
        return error_response
    
    def _handle_tool_not_found_error(
        self,
        error_response: Dict[str, Any],
        tool_name: str,
        agent_context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Handle tool not found errors with alternative suggestions"""
        
        error_response["message"] = f"Tool '{tool_name}' not found or not accessible"
        
        # Suggest similar tool names or common alternatives
        common_tool_alternatives = {
            "file": ["file_system"],
            "files": ["file_system"],
            "search": ["web_search", "knowledge_base"],
            "google": ["web_search"],
            "team": ["manage_team"],
            "agents": ["manage_team"],
            "tasks": ["project_management"],
            "project": ["project_management"],
            "message": ["send_message"],
            "help": ["system_help", "tool_information"]
        }
        
        suggestions = ["Use 'tool_information' with action 'list_tools' to see all available tools"]
        
        tool_lower = tool_name.lower()
        for key, alternatives in common_tool_alternatives.items():
            if key in tool_lower:
                suggestions.append(f"Did you mean: {', '.join(alternatives)}?")
                error_response["alternative_tools"] = alternatives
                break
                
        error_response["suggestions"] = suggestions
        return error_response
    
    def record_error_pattern(self, error_pattern: str, agent_id: str):
        """Record error patterns for learning"""
        if error_pattern not in self.error_patterns:
            self.error_patterns[error_pattern] = {"count": 0, "agents": set()}
            
        self.error_patterns[error_pattern]["count"] += 1
        self.error_patterns[error_pattern]["agents"].add(agent_id)
        
    def record_success_pattern(self, success_pattern: str, agent_id: str):
        """Record successful corrections for learning"""
        if success_pattern not in self.success_patterns:
            self.success_patterns[success_pattern] = {"count": 0, "agents": set()}
            
        self.success_patterns[success_pattern]["count"] += 1
        self.success_patterns[success_pattern]["agents"].add(agent_id)
    
    def format_error_for_agent(self, error_response: Dict[str, Any]) -> str:
        """Format error response for agent consumption"""
        
        message_parts = [f"[Tool Error: {error_response['tool_name']}]"]
        message_parts.append(error_response["message"])
        
        if error_response.get("suggestions"):
            message_parts.append("\n[Suggestions]")
            for i, suggestion in enumerate(error_response["suggestions"][:3], 1):
                message_parts.append(f"{i}. {suggestion}")
                
        if error_response.get("corrected_examples"):
            message_parts.append(f"\n[Correct Usage Example]")
            message_parts.append(error_response["corrected_examples"][0])
            
        if error_response.get("alternative_tools"):
            message_parts.append(f"\n[Alternative Tools]: {', '.join(error_response['alternative_tools'])}")
            
        return "\n".join(message_parts)

# Global instance
tool_error_handler = ToolErrorHandler()
