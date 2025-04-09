# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # For PROJECTS_BASE_DIR

logger = logging.getLogger(__name__)

class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, and listing files within an agent's private sandbox
    OR a shared session workspace.
    Ensures operations are restricted to the designated scope and path.
    """
    name: str = "file_system"
    description: str = (
        "Reads, writes, or lists files/directories. Use the 'scope' parameter to specify "
        "either the agent's 'private' sandbox or the 'shared' project session workspace. "
        "Defaults to 'private'. Use 'read' to get content, 'write' to save content, 'list' "
        "to see directory contents. All paths are relative to the root of the selected scope."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'read', 'write', or 'list'.",
            required=True,
        ),
        # --- NEW scope parameter ---
        ToolParameter(
            name="scope",
            type="string",
            description="Workspace scope: 'private' (agent's sandbox) or 'shared' (session workspace). Defaults to 'private'.",
            required=False, # Default to private
        ),
        # --- End new parameter ---
        ToolParameter(
            name="filename",
            type="string",
            description="The name of the file (relative path within the scope) to read from or write to. Required for 'read' and 'write'.",
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
            description="The sub-directory path within the scope to list. Defaults to '.' (the root). Optional for 'list'.",
            required=False,
        ),
    ]

    # --- Modified execute signature ---
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

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The absolute path to the agent's private sandbox directory.
            project_name (Optional[str]): Current project name (needed for 'shared' scope).
            session_name (Optional[str]): Current session name (needed for 'shared' scope).
            **kwargs: Arguments containing 'action', 'scope', and other relevant parameters.

        Returns:
            str: Result of the operation (file content, list of files, success message) or an error message.
        """
        action = kwargs.get("action")
        scope = kwargs.get("scope", "private").lower() # Default to private if not provided
        filename = kwargs.get("filename")
        content = kwargs.get("content")
        relative_path = kwargs.get("path", ".") # Default to current directory for list

        if not action or action not in ["read", "write", "list"]:
            return "Error: Invalid or missing 'action'. Must be 'read', 'write', or 'list'."
        if scope not in ["private", "shared"]:
            return "Error: Invalid 'scope'. Must be 'private' or 'shared'."

        # --- Determine the base path based on scope ---
        base_path: Optional[Path] = None
        scope_description: str = "" # For logging/error messages

        if scope == "private":
            base_path = agent_sandbox_path
            scope_description = f"agent {agent_id}'s private sandbox"
            # Ensure private sandbox exists (should already, but check)
            if not base_path.is_dir():
                 logger.error(f"FileSystemTool error for agent {agent_id}: Private sandbox directory {base_path} does not exist.")
                 return f"Error: Agent's private sandbox directory does not exist at {base_path}"

        elif scope == "shared":
            if not project_name or not session_name:
                 logger.error(f"FileSystemTool error for agent {agent_id}: Missing project/session context for 'shared' scope.")
                 return "Error: Cannot use 'shared' scope - project/session context is missing."
            # Define shared workspace path
            base_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "shared_workspace"
            scope_description = f"shared workspace for '{project_name}/{session_name}'"
            # Ensure shared workspace and output directory exist
            try:
                output_path = base_path / "output"
                await asyncio.to_thread(output_path.mkdir, parents=True, exist_ok=True)
                logger.debug(f"Ensured shared workspace exists: {base_path}")
                logger.debug(f"Ensured shared output directory exists: {output_path}")
            except Exception as e:
                logger.error(f"Failed to create shared workspace directories for {scope_description}: {e}", exc_info=True)
                return f"Error: Could not create shared workspace directory: {e}"
        # --- End base path determination ---

        if base_path is None: # Should not happen if logic above is correct
            return "Error: Internal error determining workspace path."

        # --- Execute action using the determined base_path ---
        try:
            if action == "read":
                if not filename:
                    return "Error: 'filename' is required for the 'read' action."
                return await self._read_file(base_path, filename, agent_id, scope_description)
            elif action == "write":
                if not filename:
                    return "Error: 'filename' is required for the 'write' action."
                if content is None:
                    return "Error: 'content' is required for the 'write' action."
                return await self._write_file(base_path, filename, content, agent_id, scope_description)
            elif action == "list":
                return await self._list_directory(base_path, relative_path, agent_id, scope_description)

        except Exception as e:
            logger.error(f"Unexpected error executing file system tool (Action: {action}, Scope: {scope}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing file system tool ({action} in {scope}): {type(e).__name__} - {e}"

        # Should not be reached
        return "Error: Unknown state in file system tool."


    # --- Modified internal methods to accept base_path and scope_description ---

    async def _resolve_and_validate_path(self, base_path: Path, relative_file_path: str, agent_id: str, scope_description: str) -> Path | None:
        """
        Resolves the relative path against the base path (private or shared) and validates it.
        """
        if not relative_file_path:
             logger.warning(f"Agent {agent_id} provided empty path/filename for {scope_description}.")
             return None
        try:
            # Normalize '.' -> current dir (represented by base_path)
            norm_relative_path = Path(relative_file_path) if relative_file_path != '.' else Path()

            # Resolve the absolute path
            absolute_path = (base_path / norm_relative_path).resolve()

            # SECURITY CHECK: Ensure the resolved path is *within* or *is* the base directory
            base_path_resolved = base_path.resolve()
            if absolute_path == base_path_resolved or absolute_path.is_relative_to(base_path_resolved):
                 return absolute_path
            else:
                logger.warning(f"Agent {agent_id} path traversal attempt blocked for {scope_description}: Resolved path {absolute_path} vs Base path {base_path_resolved}")
                return None

        except ValueError as ve: # is_relative_to can raise ValueError on Windows if drives differ
             logger.warning(f"Agent {agent_id} path validation error for {scope_description} ({ve}): Resolved path {absolute_path}, Base: {base_path_resolved}")
             return None
        except Exception as e:
            logger.error(f"Error resolving/validating path '{relative_file_path}' for agent {agent_id} within {scope_description} ('{base_path}'): {e}", exc_info=True)
            return None


    async def _read_file(self, base_path: Path, filename: str, agent_id: str, scope_description: str) -> str:
        """Reads content from a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}' in {scope_description}."

        if not validated_path.is_file():
             return f"Error: File not found or is not a regular file at '{filename}' in {scope_description}."

        try:
            content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully read file: '{filename}' from {scope_description}")
            # Optional: Limit file size read?
            return content
        except FileNotFoundError: # Should be caught by is_file() check, but good fallback
             logger.warning(f"Agent {agent_id} file read error (FileNotFound): File not found at '{filename}' in {scope_description}.")
             return f"Error: File not found at '{filename}' in {scope_description}."
        except PermissionError:
            logger.error(f"Agent {agent_id} file read error: Permission denied for '{filename}' in {scope_description}.")
            return f"Error: Permission denied when reading file '{filename}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error reading file '{filename}' in {scope_description}: {e}", exc_info=True)
            return f"Error reading file '{filename}': {type(e).__name__} - {e}"


    async def _write_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str) -> str:
        """Writes content to a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}' in {scope_description}."

        if validated_path.is_dir():
             logger.warning(f"Agent {agent_id} attempted to write to directory: {filename} in {scope_description}")
             return f"Error: Cannot write file. '{filename}' points to an existing directory."

        try:
            # Ensure parent directories exist within the base path
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)

            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully wrote file: '{filename}' to {scope_description}")
            # Provide confirmation including the scope
            return f"Successfully wrote content to '{filename}' in {scope_description}."
        except PermissionError:
            logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}' in {scope_description}.")
            return f"Error: Permission denied when writing to file '{filename}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error writing file '{filename}' in {scope_description}: {e}", exc_info=True)
            return f"Error writing file '{filename}': {type(e).__name__} - {e}"


    async def _list_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> str:
        """Lists files and directories within a specified sub-directory of the base path."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path:
             return f"Error: Invalid or disallowed path '{relative_dir}' in {scope_description}."

        if not validated_path.is_dir():
             logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir} in {scope_description}")
             return f"Error: Path '{relative_dir}' does not exist or is not a directory in {scope_description}."

        try:
            items = await asyncio.to_thread(os.listdir, validated_path)

            if not items:
                logger.info(f"Agent {agent_id} listed empty directory: '{relative_dir}' in {scope_description}")
                return f"Directory '{relative_dir}' in {scope_description} is empty."

            output_lines = [f"Contents of '{relative_dir}' in {scope_description}:"]
            for item in sorted(items):
                 try:
                      item_path = validated_path / item
                      if item_path.exists(): item_type = "dir" if item_path.is_dir() else "file"
                      elif item_path.is_symlink(): item_type = "link (broken?)"
                      else: item_type = "unknown"
                      output_lines.append(f"- {item} ({item_type})")
                 except OSError as list_item_err:
                      logger.warning(f"Error accessing item '{item}' in directory '{relative_dir}' for agent {agent_id}: {list_item_err}")
                      output_lines.append(f"- {item} (error accessing)")

            logger.info(f"Agent {agent_id} successfully listed directory: '{relative_dir}' in {scope_description}")
            return "\n".join(output_lines)

        except PermissionError:
            logger.error(f"Agent {agent_id} directory list error: Permission denied for '{relative_dir}' in {scope_description}.")
            return f"Error: Permission denied when listing directory '{relative_dir}'."
        except Exception as e:
            logger.error(f"Agent {agent_id} error listing directory '{relative_dir}' in {scope_description}: {e}", exc_info=True)
            return f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"
