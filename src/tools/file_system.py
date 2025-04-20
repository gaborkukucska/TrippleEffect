# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import re # Import re for find_replace

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # For PROJECTS_BASE_DIR

logger = logging.getLogger(__name__)

class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, listing files/directories, creating directories,
    deleting files/empty directories, and modifying files within an agent's private sandbox
    OR a shared session workspace. Ensures operations are restricted to the designated scope and path.
    """
    name: str = "file_system"
    description: str = ( # Updated description
        "Reads, writes, lists files/directories, creates directories, deletes files or empty directories, "
        "or finds and replaces text within a file. "
        "Use 'scope' ('private' or 'shared') to specify the workspace. Default: 'private'. "
        "Actions: 'read' (gets content), 'write' (saves content), 'list' (shows directory contents), "
        "'mkdir' (creates a directory), 'delete' (removes file/empty dir), "
        "'find_replace' (replaces text occurrences in a file). "
        "All paths are relative to the selected scope root."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'read', 'write', 'list', 'mkdir', 'delete', 'find_replace'.", # Added mkdir, delete
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Workspace scope: 'private' (agent's sandbox) or 'shared' (session workspace). Defaults to 'private'.",
            required=False, # Default to private
        ),
        ToolParameter(
            name="filename",
            type="string",
            description="Relative path to the file within the scope. Required for 'read', 'write', 'find_replace'.",
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
            description="Relative path to a directory or file. Required for 'list', 'mkdir', 'delete'. For 'list', defaults to '.' (scope root).",
            required=False, # Dynamically required by action
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
            description="The text string to replace occurrences of 'find_text' with. Required for 'find_replace'.",
            required=False, # Dynamically required
        ),
    ]

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Any:
        """
        Executes the file system operation based on the provided action and scope.
        """
        action = kwargs.get("action")
        scope = kwargs.get("scope", "private").lower()
        filename = kwargs.get("filename") # Used by read, write, find_replace
        content = kwargs.get("content") # Used by write
        relative_path = kwargs.get("path") # Used by list, mkdir, delete. Default for list is '.', set below if needed.
        find_text = kwargs.get("find_text") # Used by find_replace
        replace_text = kwargs.get("replace_text") # Used by find_replace

        valid_actions = ["read", "write", "list", "mkdir", "delete", "find_replace"]
        if not action or action not in valid_actions:
            return f"Error: Invalid or missing 'action'. Must be one of: {', '.join(valid_actions)}."
        if scope not in ["private", "shared"]:
            return "Error: Invalid 'scope'. Must be 'private' or 'shared'."

        # Determine base path
        base_path: Optional[Path] = None
        scope_description: str = ""
        if scope == "private":
            base_path = agent_sandbox_path; scope_description = f"agent {agent_id}'s private sandbox"
            if not base_path.is_dir(): return f"Error: Agent's private sandbox directory does not exist at {base_path}"
        elif scope == "shared":
            if not project_name or not session_name: return "Error: Cannot use 'shared' scope - project/session context is missing."
            base_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{project_name}/{session_name}'"
            try:
                output_path = base_path / "output"; await asyncio.to_thread(output_path.mkdir, parents=True, exist_ok=True)
                logger.debug(f"Ensured shared workspace dirs exist: {base_path}, {output_path}")
            except Exception as e: logger.error(f"Failed to create shared workspace dirs for {scope_description}: {e}", exc_info=True); return f"Error: Could not create shared workspace directory: {e}"
        if base_path is None: return "Error: Internal error determining workspace path."

        # Default relative path for list action if not provided
        if action == "list" and relative_path is None:
             relative_path = "."

        # Execute action
        try:
            if action == "read":
                if not filename: return "Error: 'filename' parameter is required for 'read'."
                return await self._read_file(base_path, filename, agent_id, scope_description)
            elif action == "write":
                if not filename: return "Error: 'filename' parameter is required for 'write'."
                if content is None: return "Error: 'content' parameter is required for 'write'."
                return await self._write_file(base_path, filename, content, agent_id, scope_description)
            elif action == "list":
                # relative_path default handled above
                return await self._list_directory(base_path, relative_path, agent_id, scope_description)
            elif action == "find_replace":
                if not filename: return "Error: 'filename' parameter is required for 'find_replace'."
                if find_text is None: return "Error: 'find_text' parameter is required for 'find_replace'."
                if replace_text is None: return "Error: 'replace_text' parameter is required for 'find_replace'."
                return await self._find_replace_in_file(base_path, filename, find_text, replace_text, agent_id, scope_description)
            # --- NEW: Handle mkdir and delete ---
            elif action == "mkdir":
                 if not relative_path: return "Error: 'path' parameter (directory path) is required for 'mkdir'."
                 return await self._create_directory(base_path, relative_path, agent_id, scope_description)
            elif action == "delete":
                 if not relative_path: return "Error: 'path' parameter (file or directory path) is required for 'delete'."
                 return await self._delete_item(base_path, relative_path, agent_id, scope_description)
            # --- END NEW ---

        except Exception as e:
            logger.error(f"Unexpected error executing file system tool (Action: {action}, Scope: {scope}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing file system tool ({action} in {scope}): {type(e).__name__} - {e}"

        return "Error: Unknown state in file system tool." # Should not be reached


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
             logger.warning(f"Agent {agent_id} path validation error for {scope_description} ({ve}): Path '{relative_file_path}', Base: {base_path_resolved}")
             return None
        except Exception as e:
            logger.error(f"Error resolving/validating path '{relative_file_path}' for agent {agent_id} within {scope_description} ('{base_path}'): {e}", exc_info=True)
            return None


    async def _read_file(self, base_path: Path, filename: str, agent_id: str, scope_description: str) -> str:
        """Reads content from a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return f"Error: Invalid or disallowed file path '{filename}' in {scope_description}."
        if not validated_path.is_file(): return f"Error: File not found or is not a regular file at '{filename}' in {scope_description}."
        try:
            content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully read file: '{filename}' from {scope_description}")
            return content
        except FileNotFoundError: logger.warning(f"Agent {agent_id} file read error (FileNotFound): File not found at '{filename}' in {scope_description}."); return f"Error: File not found at '{filename}' in {scope_description}."
        except PermissionError: logger.error(f"Agent {agent_id} file read error: Permission denied for '{filename}' in {scope_description}."); return f"Error: Permission denied when reading file '{filename}'."
        except Exception as e: logger.error(f"Agent {agent_id} error reading file '{filename}' in {scope_description}: {e}", exc_info=True); return f"Error reading file '{filename}': {type(e).__name__} - {e}"


    async def _write_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str) -> str:
        """Writes content to a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return f"Error: Invalid or disallowed file path '{filename}' in {scope_description}."
        if validated_path.is_dir(): logger.warning(f"Agent {agent_id} attempted to write to directory: {filename} in {scope_description}"); return f"Error: Cannot write file. '{filename}' points to an existing directory."
        try:
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully wrote file: '{filename}' to {scope_description}")
            return f"Successfully wrote content to '{filename}' in {scope_description}."
        except PermissionError: logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}' in {scope_description}."); return f"Error: Permission denied when writing to file '{filename}'."
        except Exception as e: logger.error(f"Agent {agent_id} error writing file '{filename}' in {scope_description}: {e}", exc_info=True); return f"Error writing file '{filename}': {type(e).__name__} - {e}"


    async def _list_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> str:
        """Lists files and directories within a specified sub-directory of the base path."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path: return f"Error: Invalid or disallowed path '{relative_dir}' in {scope_description}."
        if not validated_path.is_dir(): logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir} in {scope_description}"); return f"Error: Path '{relative_dir}' does not exist or is not a directory in {scope_description}."
        try:
            items = await asyncio.to_thread(os.listdir, validated_path)
            if not items: logger.info(f"Agent {agent_id} listed empty directory: '{relative_dir}' in {scope_description}"); return f"Directory '{relative_dir}' in {scope_description} is empty."
            output_lines = [f"Contents of '{relative_dir}' in {scope_description}:"]
            for item in sorted(items):
                 try:
                      item_path = validated_path / item
                      if item_path.exists(): item_type = "dir" if item_path.is_dir() else "file"
                      elif item_path.is_symlink(): item_type = "link (broken?)"
                      else: item_type = "unknown"
                      output_lines.append(f"- {item} ({item_type})")
                 except OSError as list_item_err: logger.warning(f"Error accessing item '{item}' in directory '{relative_dir}' for agent {agent_id}: {list_item_err}"); output_lines.append(f"- {item} (error accessing)")
            logger.info(f"Agent {agent_id} successfully listed directory: '{relative_dir}' in {scope_description}")
            return "\n".join(output_lines)
        except PermissionError: logger.error(f"Agent {agent_id} directory list error: Permission denied for '{relative_dir}' in {scope_description}."); return f"Error: Permission denied when listing directory '{relative_dir}'."
        except Exception as e: logger.error(f"Agent {agent_id} error listing directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"


    async def _find_replace_in_file(self, base_path: Path, filename: str, find_text: str, replace_text: str, agent_id: str, scope_description: str) -> str:
        """Finds and replaces all occurrences of text in a file within the specified scope."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return f"Error: Invalid or disallowed file path '{filename}' in {scope_description}."
        if not validated_path.is_file(): return f"Error: File not found or is not a regular file at '{filename}' in {scope_description}."

        try:
            def find_replace_sync():
                original_content = validated_path.read_text(encoding='utf-8')
                new_content = original_content.replace(find_text, replace_text)
                count = original_content.count(find_text)
                if original_content == new_content: return 0 # No changes made
                else: validated_path.write_text(new_content, encoding='utf-8'); return count
            num_replacements = await asyncio.to_thread(find_replace_sync)
            if num_replacements == 0:
                logger.info(f"Agent {agent_id}: find_replace completed for '{filename}' in {scope_description}. No occurrences of '{find_text[:50]}...' found.")
                return f"Found 0 occurrences of the text in '{filename}'. No changes made."
            else:
                logger.info(f"Agent {agent_id}: Successfully replaced {num_replacements} occurrence(s) in file '{filename}' in {scope_description}.")
                return f"Successfully replaced {num_replacements} occurrence(s) in '{filename}'."
        except FileNotFoundError: logger.warning(f"Agent {agent_id} find_replace error (FileNotFound): File not found at '{filename}' in {scope_description}."); return f"Error: File not found at '{filename}' in {scope_description}."
        except PermissionError: logger.error(f"Agent {agent_id} find_replace error: Permission denied for '{filename}' in {scope_description}."); return f"Error: Permission denied when accessing file '{filename}'."
        except Exception as e: logger.error(f"Agent {agent_id} error during find/replace in file '{filename}' in {scope_description}: {e}", exc_info=True); return f"Error during find/replace in '{filename}': {type(e).__name__} - {e}"

    # --- NEW: _create_directory method ---
    async def _create_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> str:
         """Creates a directory within the specified base path."""
         validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
         if not validated_path: return f"Error: Invalid or disallowed directory path '{relative_dir}' in {scope_description}."
         if validated_path.exists():
             if validated_path.is_dir(): return f"Directory '{relative_dir}' already exists in {scope_description}."
             else: return f"Error: Cannot create directory. '{relative_dir}' points to an existing file."
         try:
             await asyncio.to_thread(validated_path.mkdir, parents=True, exist_ok=True) # exist_ok shouldn't be needed due to check, but safe
             logger.info(f"Agent {agent_id} successfully created directory: '{relative_dir}' in {scope_description}")
             return f"Successfully created directory '{relative_dir}' in {scope_description}."
         except PermissionError: logger.error(f"Agent {agent_id} directory creation error: Permission denied for '{relative_dir}' in {scope_description}."); return f"Error: Permission denied when creating directory '{relative_dir}'."
         except Exception as e: logger.error(f"Agent {agent_id} error creating directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return f"Error creating directory '{relative_dir}': {type(e).__name__} - {e}"

    # --- NEW: _delete_item method ---
    async def _delete_item(self, base_path: Path, relative_item_path: str, agent_id: str, scope_description: str) -> str:
         """Deletes a file or an empty directory within the specified base path."""
         validated_path = await self._resolve_and_validate_path(base_path, relative_item_path, agent_id, scope_description)
         if not validated_path: return f"Error: Invalid or disallowed path '{relative_item_path}' for deletion in {scope_description}."
         if not validated_path.exists(): return f"Error: Cannot delete. Path '{relative_item_path}' does not exist in {scope_description}."
         # Prevent deleting the base path itself
         if validated_path == base_path.resolve(): return f"Error: Cannot delete the root of the {scope_description}."

         try:
             if validated_path.is_file():
                 await asyncio.to_thread(validated_path.unlink)
                 logger.info(f"Agent {agent_id} successfully deleted file: '{relative_item_path}' from {scope_description}")
                 return f"Successfully deleted file '{relative_item_path}' from {scope_description}."
             elif validated_path.is_dir():
                 # Check if directory is empty
                 is_empty = not any(await asyncio.to_thread(validated_path.iterdir))
                 if is_empty:
                     await asyncio.to_thread(validated_path.rmdir)
                     logger.info(f"Agent {agent_id} successfully deleted empty directory: '{relative_item_path}' from {scope_description}")
                     return f"Successfully deleted empty directory '{relative_item_path}' from {scope_description}."
                 else:
                     logger.warning(f"Agent {agent_id} attempted to delete non-empty directory: '{relative_item_path}' in {scope_description}")
                     return f"Error: Directory '{relative_item_path}' is not empty. Cannot delete."
             else:
                 # Handle symlinks or other types if necessary, for now, treat as error
                 logger.warning(f"Agent {agent_id} attempted to delete unsupported item type at '{relative_item_path}' in {scope_description}")
                 return f"Error: Path '{relative_item_path}' is not a file or directory. Cannot delete."
         except PermissionError: logger.error(f"Agent {agent_id} deletion error: Permission denied for '{relative_item_path}' in {scope_description}."); return f"Error: Permission denied when deleting '{relative_item_path}'."
         except Exception as e: logger.error(f"Agent {agent_id} error deleting '{relative_item_path}' in {scope_description}: {e}", exc_info=True); return f"Error deleting '{relative_item_path}': {type(e).__name__} - {e}"
