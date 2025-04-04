# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter

class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, and listing files within an agent's sandboxed directory.
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
            return f"Error: Agent sandbox directory does not exist at {agent_sandbox_path}"

        try:
            if action == "read":
                if not filename:
                    return "Error: 'filename' is required for the 'read' action."
                return await self._read_file(agent_sandbox_path, filename)
            elif action == "write":
                if not filename:
                    return "Error: 'filename' is required for the 'write' action."
                # Allow content to be an empty string, but check if it exists
                if content is None:
                    return "Error: 'content' is required for the 'write' action."
                return await self._write_file(agent_sandbox_path, filename, content)
            elif action == "list":
                return await self._list_directory(agent_sandbox_path, relative_path)

        except Exception as e:
            # Catch unexpected errors during execution
            return f"Error executing file system tool ({action}): {type(e).__name__} - {e}"

        # Should not be reached
        return "Error: Unknown state in file system tool."


    async def _resolve_and_validate_path(self, sandbox_path: Path, relative_file_path: str) -> Path | None:
        """
        Resolves the relative path against the sandbox path and validates it.

        Args:
            sandbox_path: The absolute path to the agent's sandbox.
            relative_file_path: The relative path provided by the agent/tool request.

        Returns:
            The resolved absolute Path object if valid and within the sandbox, otherwise None.
        """
        try:
            # Normalize the relative path to prevent trivial traversals like "." or ""
            # os.path.normpath might be slightly better here if dealing with mixed slashes,
            # but Path.joinpath usually handles this okay. Let's keep it Path object oriented.
            # An empty path becomes "."
            norm_relative_path = Path(relative_file_path) if relative_file_path else Path(".")

            # Join with sandbox path
            absolute_path = (sandbox_path / norm_relative_path).resolve()

            # SECURITY CHECK: Ensure the resolved path is *within* the sandbox directory
            if sandbox_path.resolve() in absolute_path.parents or sandbox_path.resolve() == absolute_path:
                # Check using is_relative_to (Python 3.9+) for a more robust check
                 if absolute_path.is_relative_to(sandbox_path.resolve()):
                    return absolute_path
                 else:
                     # This case might occur for the sandbox root itself or if symlinks are involved.
                     # Let's explicitly allow the sandbox root.
                     if absolute_path == sandbox_path.resolve():
                         return absolute_path
                     else:
                         print(f"Path traversal attempt blocked: {absolute_path} is not relative to {sandbox_path.resolve()}")
                         return None

            # Path is outside the sandbox
            print(f"Path traversal attempt blocked: Resolved path {absolute_path} is outside sandbox {sandbox_path.resolve()}")
            return None

        except Exception as e:
            # Handle potential resolution errors (e.g., invalid characters)
            print(f"Error resolving or validating path '{relative_file_path}' within sandbox '{sandbox_path}': {e}")
            return None


    async def _read_file(self, sandbox_path: Path, filename: str) -> str:
        """Reads content from a file within the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, filename)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}'. Path must be within the agent's sandbox."

        if not validated_path.is_file():
             return f"Error: File not found or is not a regular file at '{filename}'."

        try:
            # Use asyncio for async file read, though sync read is often acceptable here
            async with asyncio.to_thread(validated_path.read_text, encoding='utf-8') as content:
                 # Ensure we return the content after the async context manager finishes
                 pass
            # Limit file size? For now, read the whole file.
            return content
        except FileNotFoundError:
            return f"Error: File not found at '{filename}'."
        except PermissionError:
            return f"Error: Permission denied when reading file '{filename}'."
        except Exception as e:
            return f"Error reading file '{filename}': {type(e).__name__} - {e}"


    async def _write_file(self, sandbox_path: Path, filename: str, content: str) -> str:
        """Writes content to a file within the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, filename)
        if not validated_path:
            return f"Error: Invalid or disallowed file path '{filename}'. Path must be within the agent's sandbox."

        # Prevent writing to directories
        if validated_path.is_dir():
            return f"Error: Cannot write file. '{filename}' points to an existing directory."

        try:
            # Ensure parent directories exist
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)

            # Use asyncio for async file write
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            return f"Successfully wrote content to '{filename}'."
        except PermissionError:
            return f"Error: Permission denied when writing to file '{filename}'."
        except Exception as e:
            return f"Error writing file '{filename}': {type(e).__name__} - {e}"


    async def _list_directory(self, sandbox_path: Path, relative_dir: str) -> str:
        """Lists files and directories within a specified sub-directory of the sandbox."""
        validated_path = await self._resolve_and_validate_path(sandbox_path, relative_dir)
        if not validated_path:
             return f"Error: Invalid or disallowed path '{relative_dir}'. Path must be within the agent's sandbox."

        if not validated_path.is_dir():
             return f"Error: Path '{relative_dir}' does not exist or is not a directory."

        try:
            # Get directory contents using asyncio.to_thread to avoid blocking
            items = await asyncio.to_thread(os.listdir, validated_path)

            if not items:
                return f"Directory '{relative_dir}' is empty."

            # Format the output nicely
            output_lines = [f"Contents of '{relative_dir}':"]
            for item in sorted(items):
                 item_path = validated_path / item
                 item_type = "dir" if item_path.is_dir() else "file"
                 output_lines.append(f"- {item} ({item_type})")
            return "\n".join(output_lines)

        except PermissionError:
            return f"Error: Permission denied when listing directory '{relative_dir}'."
        except Exception as e:
            return f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"
