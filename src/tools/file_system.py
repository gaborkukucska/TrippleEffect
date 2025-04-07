# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging # Added logging

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__) # Added logger

class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, and listing files within an agent's sandboxed working directory.
    Ensures operations are restricted to the designated sandbox path.
    """
    name: str = "file_system"
    description: str = (
        "Reads, writes, or lists files within the agent's sandboxed working directory. "
        "Use 'read' to get content, 'write' to save content, 'list' to see directory contents. "
        "All paths are relative to the agent's sandbox root."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'read', 'write', or 'list'.",
            required=True,
        ),
        ToolParameter(
            name="filename",
            type="string",
            description="The name of the file (relative path) to read from or write to. Required for 'read' and 'write'.",
            required=False, # Required dynamically based on action
        ),
        ToolParameter(
            name="content",
            type="string",
            description="The text content to write to the file. Required for 'write'.",
            required=False, # Required dynamically based on action
        ),
         ToolParameter(
            name="path",
            type="string",
            description="The sub-directory path within the sandbox to list. Defaults to '.' (the root). Optional for 'list'.",
            required=False,
        ),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the file system operation based on the provided action.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The absolute path to the agent's sandbox directory.
            **kwargs: Arguments containing 'action' and other relevant parameters.

        Returns:
            str: Result of the operation (file content, list of files, success message) or an error message.
        """
        action = kwargs.get("action")
        filename = kwargs.get("filename")
        content = kwargs.get("content")
        relative_path = kwargs.get("path", ".") # Default to current directory for list

        if not action or action not in ["read", "write", "list"]:
            return f"Error: Invalid or missing 'action'. Must be 'read', 'write', or 'list'."

        # Ensure sandbox exists (should have been created by AgentManager, but double-check)
        if not agent_sandbox_path.exists() or not agent_sandbox_path.is_dir():
             logger.error(f"Filesystem tool error for agent {agent_id}: Sandbox directory {agent_sandbox_path} does not exist.")
             return f"Error: Agent sandbox directory does not exist at {agent_sandbox_path}"

        try:
            if action == "read":
                if not filename:
                    return "Error: 'filename' is required for the 'read' action."
                return await self._read_file(agent_sandbox_path, filename, agent_id) # Pass agent_id for logging
            elif action == "write":
                if not filename:
                    return "Error: 'filename' is required for the 'write' action."
                # Allow content to be an empty string, but check if it exists
                if content is None:
                    return "Error: 'content' is required for the 'write' action."
                return await self._write_file(agent_sandbox_path, filename, content, agent_id) # Pass agent_id
            elif action == "list":
                return await self._list_directory(agent_sandbox_path, relative_path, agent_id) # Pass agent_id

        except Exception as e:
            # Catch unexpected errors during execution
            logger.error(f"Unexpected error executing file system tool ({action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing file system tool ({action}): {type(e).__name__} - {e}"

        # Should not be reached
        return "Error: Unknown state in file system tool."


    async def _resolve_and_validate_path(self, sandbox_path: Path, relative_file_path: str, agent_id: str) -> Path | None:
        """
        Resolves the relative path against the sandbox path and validates it.

        Args:
            sandbox_path: The absolute path to the agent's sandbox.
            relative_file_path: The relative path provided by the agent/tool request.
            agent_id: The agent ID for logging purposes.

        Returns:
            The resolved absolute Path object if valid and within the sandbox, otherwise None.
        """
        if not relative_file_path: # Treat empty filename/path as invalid for read/write/list
             logger.warning(f"Agent {agent_id} provided empty path/filename.")
             return None
        try:
            # Normalize the relative path to prevent trivial traversals like "." or ""
            norm_relative_path = Path(relative_file_path)
            if not norm_relative_path or str(norm_relative_path) == '.':
                 # Allow '.' only for the list action
                 if action == 'list' and relative_file_path == '.':
                     pass # Allow listing the root
                 else:
                      logger.warning(f"Agent {agent_id} provided invalid relative path: '{relative_file_path}'")
                      return None

            absolute_path = (sandbox_path / norm_relative_path).resolve()

            # SECURITY CHECK: Ensure the resolved path is *within* the sandbox directory
            if sandbox_path.resolve() in absolute_path.parents or sandbox_path.resolve() == absolute_path:
                # Use is_relative_to (Python 3.9+) for a more robust check
                 try:
                     # Check if the resolved path is relative to the sandbox root
                     # This handles symlinks pointing outside correctly in most cases
                     if absolute_path.is_relative_to(sandbox_path.resolve()):
                         return absolute_path
                     # Allow access to the sandbox root itself
                     elif absolute_path == sandbox_path.resolve():
                          return absolute_path
                     else:
                         logger.warning(f"Agent {agent_id} path traversal attempt blocked (not relative): {absolute_path} vs {sandbox_path.resolve()}")
                         return None
                 except ValueError as ve: # Catches case where paths are on different drives on Windows
                     logger.warning(f"Agent {agent_id} path validation error ({ve}): Resolved path {absolute_path}, Sandbox: {sandbox_path.resolve()}")
                     return None
            else:
                # Path is outside the sandbox
                logger.warning(f"Agent {agent_id} path traversal attempt blocked (outside parent): Resolved path {absolute_path} vs sandbox {sandbox_path.resolve()}")
                return None

        except Exception as e:
            # Handle potential resolution errors (e.g., invalid characters)
            logger.error(f"Error resolving/validating path '{relative_file_path}' for agent {agent_id} within sandbox '{sandbox_path}': {e}", exc_info=True)
            return None


    # --- *** CORRECTED _read_file METHOD AGAIN *** ---
    async def _read_file(self, sandbox_path: Path, filename: str, agent_id: str) -> str:
        """Reads content from a file within the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, filename, agent_id)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}'. Path must be within the agent's sandbox."

        if not validated_path.is_file():
             return f"Error: File not found or is not a regular file at '{filename}'."

        try:
            # Correctly use asyncio.to_thread and await the result
            content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully read file: {filename}")
            # Optional: Limit file size read?
            # MAX_READ_SIZE = 1024 * 1024 # 1MB example
            # if len(content) > MAX_READ_SIZE:
            #     logger.warning(f"Agent {agent_id} read large file '{filename}', truncated to {MAX_READ_SIZE} bytes.")
            #     return content[:MAX_READ_SIZE] + "\n[... File truncated ...]"
            return content
        except FileNotFoundError:
             logger.warning(f"Agent {agent_id} file read error: File not found at '{filename}'.")
             return f"Error: File not found at '{filename}'."
        except PermissionError:
            logger.error(f"Agent {agent_id} file read error: Permission denied for '{filename}'.")
            return f"Error: Permission denied when reading file '{filename}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error reading file '{filename}': {e}", exc_info=True)
            return f"Error reading file '{filename}': {type(e).__name__} - {e}"
    # --- *** END CORRECTION *** ---


    async def _write_file(self, sandbox_path: Path, filename: str, content: str, agent_id: str) -> str:
        """Writes content to a file within the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, filename, agent_id)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}'. Path must be within the agent's sandbox."

        # Prevent writing to directories
        if validated_path.is_dir():
             logger.warning(f"Agent {agent_id} attempted to write to directory: {filename}")
             return f"Error: Cannot write file. '{filename}' points to an existing directory."

        try:
            # Ensure parent directories exist
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)

            # Use asyncio for async file write
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully wrote file: {filename}")
            return f"Successfully wrote content to '{filename}'."
        except PermissionError:
            logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}'.")
            return f"Error: Permission denied when writing to file '{filename}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error writing file '{filename}': {e}", exc_info=True)
            return f"Error writing file '{filename}': {type(e).__name__} - {e}"


    async def _list_directory(self, sandbox_path: Path, relative_dir: str, agent_id: str) -> str:
        """Lists files and directories within a specified sub-directory of the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, relative_dir, agent_id)
        if not validated_path:
             return f"Error: Invalid or disallowed path '{relative_dir}'. Path must be within the agent's sandbox."

        if not validated_path.is_dir():
             logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir}")
             return f"Error: Path '{relative_dir}' does not exist or is not a directory."

        try:
            # Get directory contents using asyncio.to_thread to avoid blocking
            items = await asyncio.to_thread(os.listdir, validated_path)

            if not items:
                logger.info(f"Agent {agent_id} listed empty directory: {relative_dir}")
                return f"Directory '{relative_dir}' is empty."

            # Format the output nicely
            output_lines = [f"Contents of '{relative_dir}':"]
            for item in sorted(items):
                 try:
                      item_path = validated_path / item
                      # Check if path exists before checking type (handles broken symlinks)
                      if item_path.exists():
                           item_type = "dir" if item_path.is_dir() else "file"
                      elif item_path.is_symlink():
                           item_type = "link (broken?)"
                      else:
                           item_type = "unknown"
                      output_lines.append(f"- {item} ({item_type})")
                 except OSError as list_item_err: # Handle potential errors accessing specific items
                      logger.warning(f"Error accessing item '{item}' in directory '{relative_dir}' for agent {agent_id}: {list_item_err}")
                      output_lines.append(f"- {item} (error accessing)")

            logger.info(f"Agent {agent_id} successfully listed directory: {relative_dir}")
            return "\n".join(output_lines)

        except PermissionError:
            logger.error(f"Agent {agent_id} directory list error: Permission denied for '{relative_dir}'.")
            return f"Error: Permission denied when listing directory '{relative_dir}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error listing directory '{relative_dir}': {e}", exc_info=True)
            return f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"
