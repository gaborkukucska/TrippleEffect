# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import re # Import re for find_replace
import shutil # Added for copy and move

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # For PROJECTS_BASE_DIR

logger = logging.getLogger(__name__)

class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, listing files/directories, creating directories,
    deleting files/empty directories, and modifying files within different scopes:
    agent's private sandbox, a shared session workspace, or the main projects directory.
    Ensures operations are restricted to the designated scope and path.
    """
    name: str = "file_system"
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Performs file system operations (read, write, append, insert_lines, replace_lines, list, mkdir, delete, find_replace, regex_replace, copy, move) within allowed scopes. Use tool_information with sub_action for per-action help."
    description: str = ( # Updated description
        "Reads, writes, appends, inserts, or replaces lines in files. Also lists, creates, or moves files/directories. "
        "Use 'scope' ('private', 'shared', or 'projects') to specify the target area. Default: 'private'. "
        "Actions: 'read', 'write' (full overwrite), 'append' (add to end), 'insert_lines', 'replace_lines', "
        "'list', 'mkdir', 'delete', 'find_replace', 'regex_replace', 'copy', 'move'. "
        "All paths are relative to the selected scope."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'read', 'write', 'list', 'mkdir', 'delete', 'find_replace', 'regex_replace', 'copy', 'move'.",
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Target scope: 'private' (agent's sandbox), 'shared' (session workspace), or 'projects' (top-level project list). Defaults to 'shared' for project workers, else 'private'.",
            required=False, # Dynamically defaulted
        ),
        ToolParameter(
            name="filename",
            type="string", 
            description="Relative path to the file within the scope. Required for 'read', 'write', 'find_replace', 'regex_replace'. Can also use 'filepath'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Text content to write. Required for 'write'.",
            required=False, # Dynamically required
        ),
         ToolParameter(
            name="path",
            type="string",
            description="Relative path to a directory or file. Required for 'list', 'mkdir', 'delete', 'copy' (as source), 'move' (as source). For 'list', defaults to '.' (scope root).",
            required=False, # Dynamically required by action
        ),
        ToolParameter(
            name="destination_path",
            type="string",
            description="The target relative path. Required for 'copy', 'move'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="find_text",
            type="string",
            description="The exact text string to find within the file. Required for 'find_replace'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="replace_text",
            type="string",
            description="The text to replace matches with. Required for 'find_replace', 'regex_replace'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="regex_pattern",
            type="string",
            description="The regular expression pattern to find. Required for 'regex_replace'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="start_line",
            type="integer",
            description="Optional starting line number (1-indexed) for 'read'.",
            required=False,
        ),
        ToolParameter(
            name="end_line",
            type="integer",
            description="Optional ending line number (1-indexed) for 'read'.",
            required=False,
        ),
        ToolParameter(
            name="insert_line",
            type="integer",
            description="Line number (1-indexed) where content should be inserted for 'insert_lines'.",
            required=False,
        ),
        ToolParameter(
            name="replace_start_line",
            type="integer",
            description="Starting line number (1-indexed, inclusive) for 'replace_lines'.",
            required=False,
        ),
        ToolParameter(
            name="replace_end_line",
            type="integer",
            description="Ending line number (1-indexed, inclusive) for 'replace_lines'.",
            required=False,
        ),
    ]

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Dict[str, Any]:
        """
        Executes the file system operation based on the provided action and scope.
        """
        action = kwargs.get("action")
        # Default to 'shared' if we have project context, else 'private'
        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()
        # Handle both 'filename' and 'filepath' parameters
        filename = kwargs.get("filename") or kwargs.get("filepath") # Used by read, write, find_replace, regex_replace
        content = kwargs.get("content") # Used by write
        relative_path = kwargs.get("path") # Used by list, mkdir, delete, copy, move. Default for list is '.', set below if needed.
        destination_path = kwargs.get("destination_path") # Used by copy, move
        find_text = kwargs.get("find_text") # Used by find_replace
        replace_text = kwargs.get("replace_text") # Used by find_replace, regex_replace
        regex_pattern = kwargs.get("regex_pattern") # Used by regex_replace
        start_line = kwargs.get("start_line") # Used by read
        end_line = kwargs.get("end_line") # Used by read
        insert_line = kwargs.get("insert_line") # Used by insert_lines
        replace_start_line = kwargs.get("replace_start_line") # Used by replace_lines
        replace_end_line = kwargs.get("replace_end_line") # Used by replace_lines

        if action == "write_file":
            action = "write"
        valid_actions = ["read", "write", "append", "insert_lines", "replace_lines", "list", "mkdir", "delete", "find_replace", "regex_replace", "copy", "move"]
        
        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "create_directory": "mkdir",
            "create_file": "write", 
            "create": "write",
            "create_folder": "mkdir",
            "make_directory": "mkdir",
            "make_dir": "mkdir",
            "new_file": "write",
            "save_file": "write",
            "save": "write",
            "read_file": "read",
            "list_files": "list",
            "list_directory": "list",
            "delete_file": "delete",
            "remove_file": "delete",
            "remove": "delete"
        }
        
        # Track whether an auto-correction was applied for feedback
        auto_corrected_from: Optional[str] = None
        
        if not action:
            return {"status": "error", "message": f"Missing required 'action' parameter. Must be one of: {', '.join(valid_actions)}."}
        
        if action not in valid_actions:
            if action in action_suggestions:
                corrected_action = action_suggestions[action]
                logger.info(f"FileSystemTool: Auto-correcting action '{action}' to '{corrected_action}' for agent '{agent_id}'.")
                auto_corrected_from = action
                action = corrected_action  # Auto-correct and continue execution
            else:
                return {"status": "error", "message": f"Invalid action '{action}'. Valid actions are: {', '.join(valid_actions)}."}
        if scope not in ["private", "shared", "projects"]: # Added 'projects' scope
            return {"status": "error", "message": "Invalid 'scope'. Must be 'private', 'shared', or 'projects'."}

        # Determine base path
        base_path: Optional[Path] = None
        scope_description: str = ""
        if scope == "private":
            base_path = agent_sandbox_path; scope_description = f"agent {agent_id}'s private sandbox"
            if not base_path.is_dir(): return {"status": "error", "message": f"Agent's private sandbox directory does not exist at {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Cannot use 'shared' scope - project/session context is missing."}
            base_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{project_name}/{session_name}'"
            try:
                # Ensure shared workspace base exists (don't need output subdir here)
                await asyncio.to_thread(base_path.mkdir, parents=True, exist_ok=True)
                logger.debug(f"Ensured shared workspace dir exists: {base_path}")
            except Exception as e: logger.error(f"Failed to create shared workspace dir for {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Could not create shared workspace directory: {e}"}
        elif scope == "projects": # Handle new 'projects' scope
            base_path = settings.PROJECTS_BASE_DIR
            scope_description = "main projects directory"
            if not base_path.is_dir(): return {"status": "error", "message": f"Main projects directory does not exist at {base_path}"}
        # --- End Scope Handling ---
        if base_path is None: return {"status": "error", "message": "Internal error determining workspace path."}

        # Default relative path for list action if not provided
        if action == "list" and relative_path is None:
             relative_path = "."

        # Execute action
        result: Optional[Dict[str, Any]] = None
        try:
            if action == "read":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'read'."}
                result = await self._read_file(base_path, filename, start_line, end_line, agent_id, scope_description)
            elif action == "write":
                if not filename: return {"status": "error", "message": "'filename' (or 'filepath') parameter is required for 'write'."}
                if filename.endswith('/'):
                    return {"status": "error", "message": f"The path '{filename}' appears to be a directory. Please use the 'mkdir' action to create directories."}
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'write'."}
                result = await self._write_file(base_path, filename, content, agent_id, scope_description)
            elif action == "list":
                # relative_path default handled above
                result = await self._list_directory(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "find_replace":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'find_replace'."}
                if find_text is None: return {"status": "error", "message": "'find_text' parameter is required for 'find_replace'."}
                if replace_text is None: return {"status": "error", "message": "'replace_text' parameter is required for 'find_replace'."}
                result = await self._find_replace_in_file(base_path, filename, find_text, replace_text, agent_id, scope_description)
            elif action == "regex_replace":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'regex_replace'."}
                if regex_pattern is None: return {"status": "error", "message": "'regex_pattern' parameter is required for 'regex_replace'."}
                if replace_text is None: return {"status": "error", "message": "'replace_text' parameter is required for 'regex_replace'."}
                result = await self._regex_replace_in_file(base_path, filename, regex_pattern, replace_text, agent_id, scope_description)
            elif action == "append":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'append'."}
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'append'."}
                result = await self._append_to_file(base_path, filename, content, agent_id, scope_description)
            elif action == "insert_lines":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'insert_lines'."}
                if insert_line is None: return {"status": "error", "message": "'insert_line' parameter is required for 'insert_lines'."}
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'insert_lines'."}
                result = await self._insert_lines_in_file(base_path, filename, insert_line, content, agent_id, scope_description)
            elif action == "replace_lines":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'replace_lines'."}
                if replace_start_line is None or replace_end_line is None: return {"status": "error", "message": "'replace_start_line' and 'replace_end_line' parameters are required for 'replace_lines'."}
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'replace_lines'."}
                result = await self._replace_lines_in_file(base_path, filename, replace_start_line, replace_end_line, content, agent_id, scope_description)

            # --- NEW: Handle mkdir and delete ---
            elif action == "mkdir":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (directory path) is required for 'mkdir'."}
                 result = await self._create_directory(base_path, relative_path, agent_id, scope_description)
            elif action == "delete":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (file or directory path) is required for 'delete'."}
                 result = await self._delete_item(base_path, relative_path, agent_id, scope_description)
            elif action == "copy":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (source) is required for 'copy'."}
                 if not destination_path: return {"status": "error", "message": "'destination_path' parameter is required for 'copy'."}
                 result = await self._copy_item(base_path, relative_path, destination_path, agent_id, scope_description)
            elif action == "move":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (source) is required for 'move'."}
                 if not destination_path: return {"status": "error", "message": "'destination_path' parameter is required for 'move'."}
                 result = await self._move_item(base_path, relative_path, destination_path, agent_id, scope_description)
            # --- END NEW ---

        except Exception as e:
            logger.error(f"Unexpected error executing file system tool (Action: {action}, Scope: {scope}) for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing file system tool ({action} in {scope}): {type(e).__name__} - {e}"}

        if result is None:
            return {"status": "error", "message": "Unknown state in file system tool."} # Should not be reached

        # Append auto-correction feedback to successful results so the agent learns
        if auto_corrected_from and result.get("status") == "success":
            correction_note = (
                f" [Note: The action '{auto_corrected_from}' was auto-corrected to '{action}'. "
                f"Please use '{action}' directly in future calls.]"
            )
            result["message"] = result.get("message", "") + correction_note

        return result

    # --- Detailed Usage Method ---
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the FileSystemTool."""
        
        common_header = """**Tool Name:** file_system

**Description:** Performs operations on files and directories within different scopes. All paths MUST be relative to the scope root.

**CRITICAL - Valid Actions Only:** The following actions are the ONLY valid actions:
read, write, append, insert_lines, replace_lines, list, mkdir, delete, find_replace, regex_replace, copy, move

**Scopes:**
*   `private`: Agent's own sandbox directory (Default).
*   `shared`: Current session's shared workspace. Requires project/session context.
*   `projects`: Top-level projects directory (ONLY use with `list` action).
"""
        
        if sub_action == "read":
            return common_header + """
**Action: read**
Reads the content of a file.
*   `<filename>` (string, required): Relative path to the file.
*   `<start_line>` (integer, optional): Line number to start reading from (1-indexed).
*   `<end_line>` (integer, optional): Line number to stop reading at (inclusive).
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "write":
            return common_header + """
**Action: write**
Writes content to a file. Overwrites completely. For targeted edits, use `replace_lines` or `append` instead!
*   `<filename>` (string, required): Relative path to the file.
*   `<content>` (string, required): The complete file content to write.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "append":
            return common_header + """
**Action: append**
Appends text to the end of an existing file.
*   `<filename>` (string, required): Relative path to the file.
*   `<content>` (string, required): Text to append.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "insert_lines":
            return common_header + """
**Action: insert_lines**
Inserts a block of text at a specific line number, shifting subsequent lines down.
*   `<filename>` (string, required): Relative path to the file.
*   `<insert_line>` (integer, required): The line number (1-indexed) where the new `<content>` will be inserted.
*   `<content>` (string, required): Text to insert.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "replace_lines":
            return common_header + """
**Action: replace_lines**
Replaces a specific block of existing lines with new content. STRONGLY RECOMMENDED for modifying existing files.
*   `<filename>` (string, required): Relative path to the file.
*   `<replace_start_line>` (integer, required): The first line number (1-indexed) to remove.
*   `<replace_end_line>` (integer, required): The last line number (1-indexed) to remove (inclusive).
*   `<content>` (string, required): The new text to replace the removed lines with.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "list":
            return common_header + """
**Action: list**
Lists files and directories within a path.
*   `<path>` (string, optional): Relative directory path. Defaults to root of scope.
*   `<scope>` (string, optional): 'private', 'shared', 'projects'. Default: 'private'.
"""
        elif sub_action == "mkdir":
            return common_header + """
**Action: mkdir**
Creates a directory, including parent directories.
*   `<path>` (string, required): Relative directory path.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "delete":
            return common_header + """
**Action: delete**
Deletes a file or an *empty* directory.
*   `<path>` (string, required): Relative target path.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "find_replace":
            return common_header + """
**Action: find_replace**
Finds and replaces all occurrences of an EXACT string.
*   `<filename>` (string, required): Relative path.
*   `<find_text>` (string, required): Exact string to find.
*   `<replace_text>` (string, required): Replacement string.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "regex_replace":
            return common_header + """
**Action: regex_replace**
Finds and replaces using regex.
*   `<filename>` (string, required): Relative path.
*   `<regex_pattern>` (string, required): Regex pattern.
*   `<replace_text>` (string, required): Replacement string.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "copy":
            return common_header + """
**Action: copy**
Copies a file/directory.
*   `<path>` (string, required): Source path.
*   `<destination_path>` (string, required): Target path.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "move":
            return common_header + """
**Action: move**
Moves/renames a file/directory.
*   `<path>` (string, required): Source path.
*   `<destination_path>` (string, required): Target path.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""

        # Return directory of commands
        return common_header + """
**Available Actions Summary:**
1.  **read:** Reads file content. (Use start_line and end_line for large files).
2.  **write:** Overwrites a file completely.
3.  **append:** Adds text to the end of a file.
4.  **insert_lines:** Inserts a block of text at a specific line.
5.  **replace_lines:** Replaces a block of lines with new text.
6.  **list:** Lists files in a directory.
7.  **mkdir:** Creates a directory.
8.  **delete:** Deletes a file or empty directory.
9.  **find_replace:** Exact string replacement.
10. **regex_replace:** Regex based string replacement.
11. **copy:** Copies files/directories.
12. **move:** Moves/renames files/directories.

**To get detailed instructions and parameter lists for a specific action, call:**
<tool_information>
  <action>get_info</action>
  <tool_name>file_system</tool_name>
  <sub_action>ACTION_NAME</sub_action>
</tool_information>
"""

    async def _resolve_and_validate_path(self, base_path: Path, relative_file_path: str, agent_id: str, scope_description: str) -> Path | None:
        """ Resolves the relative path against the base path and validates it. """
        if not relative_file_path: logger.warning(f"Agent {agent_id} provided empty path/filename for {scope_description}."); return None
        try:
            # Handle potential path normalization issues
            # Disallow paths starting with '/' or containing '..'
            if relative_file_path.startswith('/') or '..' in Path(relative_file_path).parts:
                 logger.warning(f"Agent {agent_id} provided potentially unsafe path '{relative_file_path}' for {scope_description}.")
                 return None

            norm_relative_path = Path(relative_file_path)
            absolute_path = (base_path / norm_relative_path).resolve()
            base_path_resolved = base_path.resolve()

            # SECURITY CHECK: Ensure the resolved path is *within* or *is* the base directory
            if absolute_path == base_path_resolved or absolute_path.is_relative_to(base_path_resolved):
                 return absolute_path
            else:
                logger.warning(f"Agent {agent_id} path traversal attempt blocked for {scope_description}: Resolved path {absolute_path} vs Base path {base_path_resolved}")
                return None
        except ValueError as ve: # is_relative_to can raise ValueError on Windows if drives differ
             logger.warning(f"Agent {agent_id} path validation error for {scope_description} ({ve}): Path '{relative_file_path}'")
             return None
        except Exception as e:
            logger.error(f"Error resolving/validating path '{relative_file_path}' for agent {agent_id} within {scope_description} ('{base_path}'): {e}", exc_info=True)
            return None


    async def _read_file(self, base_path: Path, filename: str, start_line: Optional[int], end_line: Optional[int], agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Reads content from a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if not validated_path.is_file(): return {"status": "error", "message": f"File not found or is not a regular file at '{filename}' in {scope_description}."}
        try:
            content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            if start_line is not None or end_line is not None:
                lines = content.splitlines(True)
                start_idx = max(0, start_line - 1) if start_line is not None else 0
                end_idx = min(len(lines), end_line) if end_line is not None else len(lines)
                if start_idx >= len(lines) or start_idx > end_idx:
                    return {"status": "error", "message": f"Invalid start_line/end_line range for file with {len(lines)} lines."}
                content = "".join(lines[start_idx:end_idx])
            logger.info(f"Agent {agent_id} successfully read file: '{filename}' from {scope_description}")
            return {"status": "success", "content": content}
        except FileNotFoundError: logger.warning(f"Agent {agent_id} file read error (FileNotFound): File not found at '{filename}' in {scope_description}."); return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}."}
        except PermissionError: logger.error(f"Agent {agent_id} file read error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when reading file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error reading file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error reading file '{filename}': {type(e).__name__} - {e}"}


    async def _write_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Writes content to a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if validated_path.is_dir(): logger.warning(f"Agent {agent_id} attempted to write to directory: {filename} in {scope_description}"); return {"status": "error", "message": f"Cannot write file. '{filename}' points to an existing directory."}
        try:
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully wrote file: '{filename}' to {scope_description}")
            return {"status": "success", "message": f"Successfully wrote content to '{filename}' in {scope_description}."}
        except PermissionError: logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when writing to file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error writing file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error writing file '{filename}': {type(e).__name__} - {e}"}


    async def _list_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Lists files and directories within a specified sub-directory of the base path."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_dir}' in {scope_description}."}
        if not validated_path.is_dir(): logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir} in {scope_description}"); return {"status": "error", "message": f"Path '{relative_dir}' does not exist or is not a directory in {scope_description}."}
        try:
            items = await asyncio.to_thread(os.listdir, validated_path)
            if not items: logger.info(f"Agent {agent_id} listed empty directory: '{relative_dir}' in {scope_description}"); return {"status": "success", "message": f"Directory '{relative_dir}' in {scope_description} is empty.", "items": []}
            output_lines = []
            item_details = []
            for item in sorted(items):
                 try:
                      item_path = validated_path / item
                      item_type = "unknown"
                      if item_path.is_symlink(): item_type = "link"
                      elif item_path.is_dir(): item_type = "dir"
                      elif item_path.is_file(): item_type = "file"
                      output_lines.append(f"- {item} ({item_type})")
                      item_details.append({"name": item, "type": item_type})
                 except OSError as list_item_err: logger.warning(f"Error accessing item '{item}' in directory '{relative_dir}' for agent {agent_id}: {list_item_err}"); output_lines.append(f"- {item} (error accessing)")
            logger.info(f"Agent {agent_id} successfully listed directory: '{relative_dir}' in {scope_description}")
            return {"status": "success", "message": f"Contents of '{relative_dir}' in {scope_description}:\n" + "\n".join(output_lines), "items": item_details}
        except PermissionError: logger.error(f"Agent {agent_id} directory list error: Permission denied for '{relative_dir}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when listing directory '{relative_dir}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error listing directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"}


    async def _find_replace_in_file(self, base_path: Path, filename: str, find_text: str, replace_text: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Finds and replaces all occurrences of text in a file within the specified scope."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if not validated_path.is_file(): return {"status": "error", "message": f"File not found or is not a regular file at '{filename}' in {scope_description}."}

        try:
            def find_replace_sync():
                original_content = validated_path.read_text(encoding='utf-8')
                new_content = original_content.replace(find_text, replace_text)
                count = original_content.count(find_text)
                if original_content == new_content: return 0 # No changes made
                else: validated_path.write_text(new_content, encoding='utf-8'); return count
            num_replacements = await asyncio.to_thread(find_replace_sync)
            message = f"Found 0 occurrences of the text in '{filename}'. No changes made."
            if num_replacements > 0:
                message = f"Successfully replaced {num_replacements} occurrence(s) in '{filename}'."
                logger.info(f"Agent {agent_id}: Successfully replaced {num_replacements} occurrence(s) in file '{filename}' in {scope_description}.")
            else:
                 logger.info(f"Agent {agent_id}: find_replace completed for '{filename}' in {scope_description}. No occurrences of '{find_text[:50]}...' found.")
            return {"status": "success", "message": message, "replacements_made": num_replacements}
        except FileNotFoundError: logger.warning(f"Agent {agent_id} find_replace error (FileNotFound): File not found at '{filename}' in {scope_description}."); return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}."}
        except PermissionError: logger.error(f"Agent {agent_id} find_replace error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when accessing file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error during find/replace in file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error during find/replace in '{filename}': {type(e).__name__} - {e}"}

    # --- NEW: _create_directory method ---
    async def _create_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
         """Creates a directory within the specified base path."""
         validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
         if not validated_path: return {"status": "error", "message": f"Invalid or disallowed directory path '{relative_dir}' in {scope_description}."}
         if validated_path.exists():
             if validated_path.is_dir(): return {"status": "success", "message": f"Directory '{relative_dir}' already exists in {scope_description}."}
             else: return {"status": "error", "message": f"Cannot create directory. '{relative_dir}' points to an existing file."}
         try:
             await asyncio.to_thread(validated_path.mkdir, parents=True, exist_ok=True) # exist_ok shouldn't be needed due to check, but safe
             logger.info(f"Agent {agent_id} successfully created directory: '{relative_dir}' in {scope_description}")
             return {"status": "success", "message": f"Successfully created directory '{relative_dir}' in {scope_description}."}
         except PermissionError: logger.error(f"Agent {agent_id} directory creation error: Permission denied for '{relative_dir}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when creating directory '{relative_dir}'."}
         except Exception as e: logger.error(f"Agent {agent_id} error creating directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error creating directory '{relative_dir}': {type(e).__name__} - {e}"}

    # --- NEW: _delete_item method ---
    async def _delete_item(self, base_path: Path, relative_item_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
         """Deletes a file or an empty directory within the specified base path."""
         validated_path = await self._resolve_and_validate_path(base_path, relative_item_path, agent_id, scope_description)
         if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_item_path}' for deletion in {scope_description}."}
         if not validated_path.exists(): return {"status": "error", "message": f"Cannot delete. Path '{relative_item_path}' does not exist in {scope_description}."}
         # Prevent deleting the base path itself
         if validated_path == base_path.resolve(): return {"status": "error", "message": f"Cannot delete the root of the {scope_description}."}

         try:
             if validated_path.is_file():
                 await asyncio.to_thread(validated_path.unlink)
                 logger.info(f"Agent {agent_id} successfully deleted file: '{relative_item_path}' from {scope_description}")
                 return {"status": "success", "message": f"Successfully deleted file '{relative_item_path}' from {scope_description}."}
             elif validated_path.is_dir():
                 # Check if directory is empty
                 is_empty = not any(await asyncio.to_thread(validated_path.iterdir))
                 if is_empty:
                     await asyncio.to_thread(validated_path.rmdir)
                     logger.info(f"Agent {agent_id} successfully deleted empty directory: '{relative_item_path}' from {scope_description}")
                     return {"status": "success", "message": f"Successfully deleted empty directory '{relative_item_path}' from {scope_description}."}
                 else:
                     logger.warning(f"Agent {agent_id} attempted to delete non-empty directory: '{relative_item_path}' in {scope_description}")
                     return {"status": "error", "message": f"Directory '{relative_item_path}' is not empty. Cannot delete."}
             else:
                 # Handle symlinks or other types if necessary, for now, treat as error
                 logger.warning(f"Agent {agent_id} attempted to delete unsupported item type at '{relative_item_path}' in {scope_description}")
                 return {"status": "error", "message": f"Path '{relative_item_path}' is not a file or directory. Cannot delete."}
         except PermissionError: logger.error(f"Agent {agent_id} deletion error: Permission denied for '{relative_item_path}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when deleting '{relative_item_path}'."}
         except Exception as e: logger.error(f"Agent {agent_id} error deleting '{relative_item_path}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error deleting '{relative_item_path}': {type(e).__name__} - {e}"}

    async def _regex_replace_in_file(self, base_path: Path, filename: str, regex_pattern: str, replace_text: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if not validated_path.is_file(): return {"status": "error", "message": f"File not found or is not a regular file at '{filename}' in {scope_description}."}
        try:
            def regex_replace_sync():
                original_content = validated_path.read_text(encoding='utf-8')
                new_content, count = re.subn(regex_pattern, replace_text, original_content, flags=re.MULTILINE)
                if count > 0: validated_path.write_text(new_content, encoding='utf-8')
                return count
            
            num_replacements = await asyncio.to_thread(regex_replace_sync)
            message = f"Found 0 occurrences matching regex '{regex_pattern}' in '{filename}'. No changes made."
            if num_replacements > 0:
                message = f"Successfully replaced {num_replacements} match(es) in '{filename}'."
                logger.info(f"Agent {agent_id}: Successfully replaced {num_replacements} match(es) in file '{filename}' in {scope_description}.")
            else:
                 logger.info(f"Agent {agent_id}: regex_replace completed for '{filename}' in {scope_description}. No matches found.")
            return {"status": "success", "message": message, "replacements_made": num_replacements}
        except FileNotFoundError: return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}."}
        except PermissionError: return {"status": "error", "message": f"Permission denied when accessing file '{filename}'."}
        except re.error as e: return {"status": "error", "message": f"Invalid regex pattern '{regex_pattern}': {e}"}
        except Exception as e: return {"status": "error", "message": f"Error during regex replace in '{filename}': {type(e).__name__} - {e}"}

    async def _copy_item(self, base_path: Path, relative_src: str, relative_dst: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_src = await self._resolve_and_validate_path(base_path, relative_src, agent_id, scope_description)
        if not val_src: return {"status": "error", "message": f"Invalid source path '{relative_src}'."}
        if not val_src.exists(): return {"status": "error", "message": f"Source '{relative_src}' does not exist."}
        val_dst = await self._resolve_and_validate_path(base_path, relative_dst, agent_id, scope_description)
        if not val_dst: return {"status": "error", "message": f"Invalid destination path '{relative_dst}'."}
        
        try:
            await asyncio.to_thread(val_dst.parent.mkdir, parents=True, exist_ok=True)
            if val_src.is_file():
                await asyncio.to_thread(shutil.copy2, val_src, val_dst)
            else:
                if val_dst.exists(): return {"status": "error", "message": f"Destination '{relative_dst}' already exists."}
                await asyncio.to_thread(shutil.copytree, val_src, val_dst)
            logger.info(f"Agent {agent_id} copied '{relative_src}' to '{relative_dst}' in {scope_description}")
            return {"status": "success", "message": f"Successfully copied '{relative_src}' to '{relative_dst}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error copying '{relative_src}' to '{relative_dst}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error copying '{relative_src}' to '{relative_dst}': {e}"}

    async def _move_item(self, base_path: Path, relative_src: str, relative_dst: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_src = await self._resolve_and_validate_path(base_path, relative_src, agent_id, scope_description)
        if not val_src: return {"status": "error", "message": f"Invalid source path '{relative_src}'."}
        if not val_src.exists(): return {"status": "error", "message": f"Source '{relative_src}' does not exist."}
        val_dst = await self._resolve_and_validate_path(base_path, relative_dst, agent_id, scope_description)
        if not val_dst: return {"status": "error", "message": f"Invalid destination path '{relative_dst}'."}
        
        try:
            await asyncio.to_thread(val_dst.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, val_src, val_dst)
            logger.info(f"Agent {agent_id} moved '{relative_src}' to '{relative_dst}' in {scope_description}")
            return {"status": "success", "message": f"Successfully moved '{relative_src}' to '{relative_dst}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error moving '{relative_src}' to '{relative_dst}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error moving '{relative_src}' to '{relative_dst}': {e}"}

    async def _append_to_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if val_path.is_dir(): return {"status": "error", "message": f"Cannot append. '{filename}' is a directory."}
        try:
            await asyncio.to_thread(val_path.parent.mkdir, parents=True, exist_ok=True)
            def append_sync():
                with open(val_path, 'a', encoding='utf-8') as f:
                    f.write(content)
            await asyncio.to_thread(append_sync)
            logger.info(f"Agent {agent_id} appended to '{filename}' in {scope_description}")
            return {"status": "success", "message": f"Successfully appended content to '{filename}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error appending to '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error appending to '{filename}': {e}"}

    async def _insert_lines_in_file(self, base_path: Path, filename: str, insert_line: int, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        try:
            def insert_sync():
                lines = val_path.read_text(encoding='utf-8').splitlines(True)
                idx = max(0, insert_line - 1)
                
                new_lines = content.splitlines(True)
                if new_lines and not new_lines[-1].endswith('\\n') and idx < len(lines):
                    new_lines[-1] += '\\n'
                    
                lines = lines[:idx] + new_lines + lines[idx:]
                val_path.write_text("".join(lines), encoding='utf-8')
            await asyncio.to_thread(insert_sync)
            logger.info(f"Agent {agent_id} inserted lines at {insert_line} in '{filename}' ({scope_description})")
            return {"status": "success", "message": f"Successfully inserted content at line {insert_line} in '{filename}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error inserting lines in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error inserting lines in '{filename}': {e}"}

    async def _replace_lines_in_file(self, base_path: Path, filename: str, start_line: int, end_line: int, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        if start_line > end_line or start_line < 1:
            return {"status": "error", "message": f"Invalid line range: {start_line} to {end_line}."}
        try:
            def replace_sync():
                lines = val_path.read_text(encoding='utf-8').splitlines(True)
                start_idx = max(0, start_line - 1)
                end_idx = max(start_idx, min(end_line, len(lines)))
                
                new_lines = content.splitlines(True)
                if new_lines and not new_lines[-1].endswith('\\n') and end_idx < len(lines):
                    new_lines[-1] += '\\n'
                    
                lines = lines[:start_idx] + new_lines + lines[end_idx:]
                val_path.write_text("".join(lines), encoding='utf-8')
            await asyncio.to_thread(replace_sync)
            logger.info(f"Agent {agent_id} replaced lines {start_line}-{end_line} in '{filename}' ({scope_description})")
            return {"status": "success", "message": f"Successfully replaced lines {start_line} to {end_line} in '{filename}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error replacing lines in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error replacing lines in '{filename}': {e}"}
