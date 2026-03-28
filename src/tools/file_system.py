# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import re # Import re for find_replace
import shutil # Added for copy and move
from diff_match_patch import diff_match_patch # Added for search_replace_block
import git # Added for git integration
from git.exc import InvalidGitRepositoryError, GitCommandError

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
        "Actions: 'read', 'write' (for NEW files only), 'append' (add to end), 'insert_lines', 'replace_lines', "
        "'list', 'mkdir', 'delete', 'find_replace', 'regex_replace', 'copy', 'move', 'search_replace_block', 'git_commit', 'git_status', 'git_diff', 'git_init'. "
        "All paths are relative to the selected scope."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'read', 'write', 'list', 'mkdir', 'delete', 'find_replace', 'regex_replace', 'copy', 'move', 'search_replace_block', 'git_commit', 'git_status', 'git_diff'.",
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
            description="Line number (1-indexed) where content should be inserted for 'insert_lines'. Alternatively, use '<search>' to find a text anchor and insert relative to it.",
            required=False,
        ),
        ToolParameter(
            name="replace_start_line",
            type="integer",
            description="Starting line number (1-indexed, inclusive) for 'replace_lines'. Also accepts 'start_line'.",
            required=False,
        ),
        ToolParameter(
            name="replace_end_line",
            type="integer",
            description="Ending line number (1-indexed, inclusive) for 'replace_lines'. Also accepts 'end_line'.",
            required=False,
        ),
        ToolParameter(
            name="search_block",
            type="string",
            description="The exact (or slightly fuzzy) block of text to find. Required for 'search_replace_block'.",
            required=False,
        ),
        ToolParameter(
            name="replace_block",
            type="string",
            description="The block of text to replace it with. Required for 'search_replace_block'.",
            required=False,
        ),
        ToolParameter(
            name="expected_replacements",
            type="integer",
            description="Optional. For 'search_replace_block': if the tool finds multiple matching blocks and reports the count N, you MUST re-send the call with this set to N to confirm replacing all N occurrences.",
            required=False,
        ),
        ToolParameter(
            name="start_marker",
            type="string",
            description="Optional for 'search_replace_block': a unique string marking the start of the block to replace. Can be used instead of 'search_block'.",
            required=False,
        ),
        ToolParameter(
            name="end_marker",
            type="string",
            description="Optional for 'search_replace_block': a unique string marking the end of the block to replace. Must be used with 'start_marker'.",
            required=False,
        ),
        ToolParameter(
            name="commit_message",
            type="string",
            description="The message for the git commit. Required for 'git_commit'.",
            required=False,
        ),
        ToolParameter(
            name="branch",
            type="string",
            description="Branch name. Used for 'git_checkout', and 'git_branch' (to create).",
            required=False,
        ),
        ToolParameter(
            name="files",
            type="string",
            description="Comma-separated files or pattern (e.g. '.'). Required for 'git_add' to stage files.",
            required=False,
        ),
        ToolParameter(
            name="remote",
            type="string",
            description="Git remote name (default: 'origin'). Used for 'git_push' and 'git_pull'.",
            required=False,
        ),
    ]

    @staticmethod
    def _first_of(*values):
        """Return the first value that is not None. Preserves empty strings and other falsy values like 0."""
        for v in values:
            if v is not None:
                return v
        return None

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
        _fo = self._first_of # Shorthand — preserves empty strings unlike `or`
        filename = _fo(kwargs.get("filename"), kwargs.get("filepath"), kwargs.get("file"), kwargs.get("path")) # Used by read, write, find_replace, regex_replace
        if isinstance(filename, str): filename = filename.strip()
        content = _fo(kwargs.get("content"), kwargs.get("text")) # Used by write, append, insert_lines
        relative_path = _fo(kwargs.get("path"), kwargs.get("filename"), kwargs.get("filepath")) # Used by list, mkdir, delete, copy, move
        if isinstance(relative_path, str): relative_path = relative_path.strip()
        destination_path = kwargs.get("destination_path") # Used by copy, move
        if isinstance(destination_path, str): destination_path = destination_path.strip()
        find_text = _fo(kwargs.get("find_text"), kwargs.get("search"), kwargs.get("find"), kwargs.get("search_string"), kwargs.get("search_text"), kwargs.get("search_term")) # Used by find_replace
        replace_text = _fo(kwargs.get("replace_text"), kwargs.get("replace"), kwargs.get("replacement"), kwargs.get("replacement_text"), kwargs.get("replace_string"), kwargs.get("replace_term")) # Used by find_replace, regex_replace
        regex_pattern = kwargs.get("regex_pattern") # Used by regex_replace
        start_line = kwargs.get("start_line") # Used by read
        end_line = kwargs.get("end_line") # Used by read
        insert_line = _fo(kwargs.get("insert_line"), kwargs.get("line_number"), kwargs.get("line"), kwargs.get("at_line")) # Used by insert_lines (NOTE: 'position' excluded — it's a semantic hint like 'after'/'before', not a line number)
        replace_start_line = _fo(kwargs.get("replace_start_line"), kwargs.get("start_line_number"), kwargs.get("from_line")) # Used by replace_lines
        replace_end_line = _fo(kwargs.get("replace_end_line"), kwargs.get("end_line_number"), kwargs.get("to_line")) # Used by replace_lines
        # Auto-cast all line-number params to int if they are numeric strings
        def _safe_int(val):
            if val is None: return None
            try: return int(val)
            except (ValueError, TypeError): return val
        start_line = _safe_int(start_line)
        end_line = _safe_int(end_line)
        insert_line = _safe_int(insert_line)  # May remain a string if it's a search hint — handled later
        replace_start_line = _safe_int(replace_start_line)
        replace_end_line = _safe_int(replace_end_line)
        search_block = _fo(kwargs.get("search_block"), kwargs.get("search"), kwargs.get("search_string"), kwargs.get("find"), kwargs.get("find_text"), kwargs.get("search_term"), kwargs.get("search_text")) # Used by search_replace_block
        replace_block_param = _fo(kwargs.get("replace_block"), kwargs.get("replace"), kwargs.get("replacement"), kwargs.get("replace_string"), kwargs.get("replace_text"), kwargs.get("replace_term")) # Used by search_replace_block
        expected_replacements = kwargs.get("expected_replacements")  # Used by search_replace_block for multi-match confirmation
        start_marker = _fo(kwargs.get("start_marker"), kwargs.get("start"), kwargs.get("start_string"), kwargs.get("start_line"), kwargs.get("start_pattern"))
        end_marker = _fo(kwargs.get("end_marker"), kwargs.get("end"), kwargs.get("end_string"), kwargs.get("end_line"), kwargs.get("end_pattern"))
        try:
            expected_replacements = int(expected_replacements) if expected_replacements is not None else None
        except (ValueError, TypeError):
            expected_replacements = None
        commit_message = kwargs.get("commit_message") # Used by git_commit
        branch = kwargs.get("branch") # Used by git_checkout, git_branch
        files_to_add = kwargs.get("files") # Used by git_add
        remote = kwargs.get("remote", "origin") # Used by git_push, git_pull

        if action == "write_file":
            action = "write"
        valid_actions = [
            "read", "write", "append", "insert_lines", "replace_lines", "search_replace_block", 
            "list", "mkdir", "delete", "find_replace", "regex_replace", "copy", "move", 
            "git_commit", "git_status", "git_diff", "git_log", "git_branch", "git_checkout", 
            "git_pull", "git_push", "git_add", "git_init"
        ]
        
        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "add_lines": "insert_lines",
            "add_line": "insert_lines",
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
            "remove": "delete",
            "insert_line": "insert_lines",
            "replace_line": "replace_lines",
            "commit": "git_commit",
            "status": "git_status",
            "diff": "git_diff",
            "log": "git_log",
            "branch": "git_branch",
            "checkout": "git_checkout",
            "pull": "git_pull",
            "push": "git_push",
            "add": "git_add",
            "init": "git_init"
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
            safe_project_name = project_name.replace(" ", "_").strip()
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{safe_project_name}/{session_name}'"
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
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'insert_lines'."}
                # Support search-based insertion: if insert_line is missing or non-numeric,
                # check if search/find_text was provided to locate the insertion point.
                position_kwarg = kwargs.get("position", "after")  # Semantic hint: 'before' or 'after'
                search_anchor = _fo(kwargs.get("search"), kwargs.get("after"), kwargs.get("before"))
                if insert_line is None or (isinstance(insert_line, str) and not insert_line.isdigit()):
                    if search_anchor:
                        # Resolve insertion point by finding the search anchor text in the file
                        resolved = await self._resolve_insert_line_from_search(
                            base_path, filename, search_anchor, agent_id, scope_description,
                            position_hint=insert_line if isinstance(insert_line, str) else position_kwarg
                        )
                        if resolved is None:
                            return {"status": "error", "message": f"Could not find search anchor '{search_anchor}' in file '{filename}' for insert_lines."}
                        insert_line = resolved
                    else:
                        return {"status": "error", "message": "'insert_line' parameter (integer line number) is required for 'insert_lines'. Alternatively, provide a '<search>' text to insert after."}
                result = await self._insert_lines_in_file(base_path, filename, int(insert_line), content, agent_id, scope_description)
            elif action == "replace_lines":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'replace_lines'."}
                # Fallback: LLMs frequently use start_line/end_line instead of replace_start_line/replace_end_line
                if replace_start_line is None and start_line is not None:
                    replace_start_line = _safe_int(start_line)
                if replace_end_line is None and end_line is not None:
                    replace_end_line = _safe_int(end_line)
                if replace_start_line is None or replace_end_line is None: return {"status": "error", "message": "'replace_start_line' and 'replace_end_line' parameters are required for 'replace_lines'."}
                if content is None: return {"status": "error", "message": "'content' parameter is required for 'replace_lines'."}
                result = await self._replace_lines_in_file(base_path, filename, replace_start_line, replace_end_line, content, agent_id, scope_description)
            elif action == "search_replace_block":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'search_replace_block'."}
                if search_block is None and (start_marker is None or end_marker is None): 
                    return {"status": "error", "message": "'search_block' OR both 'start_marker' and 'end_marker' are required for 'search_replace_block'."}
                if replace_block_param is None: return {"status": "error", "message": "'replace_block' parameter is required for 'search_replace_block'."}
                result = await self._search_replace_block_in_file(base_path, filename, search_block, replace_block_param, agent_id, scope_description, expected_replacements=expected_replacements, start_marker=start_marker, end_marker=end_marker)

            # --- Git integration ---
            elif action == "git_commit":
                if not commit_message: return {"status": "error", "message": "'commit_message' parameter is required for 'git_commit'."}
                result = await self._git_commit(base_path, relative_path if isinstance(relative_path, str) else ".", commit_message, agent_id, scope_description)
            elif action == "git_status":
                result = await self._git_status(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_diff":
                result = await self._git_diff(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_log":
                result = await self._git_log(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_branch":
                result = await self._git_branch(base_path, relative_path if isinstance(relative_path, str) else ".", branch, agent_id, scope_description)
            elif action == "git_checkout":
                if not branch: return {"status": "error", "message": "'branch' parameter is required for 'git_checkout'."}
                result = await self._git_checkout(base_path, relative_path if isinstance(relative_path, str) else ".", branch, agent_id, scope_description)
            elif action == "git_add":
                if not files_to_add: return {"status": "error", "message": "'files' parameter is required for 'git_add'."}
                result = await self._git_add(base_path, relative_path if isinstance(relative_path, str) else ".", files_to_add, agent_id, scope_description)
            elif action == "git_init":
                result = await self._git_init(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_pull":
                result = await self._git_pull(base_path, relative_path if isinstance(relative_path, str) else ".", remote, agent_id, scope_description)
            elif action == "git_push":
                result = await self._git_push(base_path, relative_path if isinstance(relative_path, str) else ".", remote, agent_id, scope_description)

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
Writes content to a NEW file (Forbidden on existing files to save tokens/prevent data loss). For targeted edits on existing files, use `replace_lines`, `find_replace`, `regex_replace`, or `append`! DO NOT use `write` to create directories — use `mkdir` instead!
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
*   `<insert_line>` (integer): The line number (1-indexed) where the new `<content>` will be inserted. **Either `<insert_line>` or `<search>` is required.**
*   `<search>` (string): Instead of a line number, provide a text string to search for in the file. The new content will be inserted relative to the first matching line.
*   `<position>` (string, optional): 'after' (default) or 'before'. Controls whether content is inserted after or before the `<search>` match.
*   `<content>` (string, required): Text to insert.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
*   Example (by line number):
    ```xml
    <file_system>
      <action>insert_lines</action>
      <filename>server.js</filename>
      <insert_line>10</insert_line>
      <content>const express = require('express');</content>
      <scope>shared</scope>
    </file_system>
    ```
*   Example (by search anchor):
    ```xml
    <file_system>
      <action>insert_lines</action>
      <filename>package.json</filename>
      <search>"dependencies": {</search>
      <position>before</position>
      <content>  "devDependencies": { "jest": "^29.0.0" },</content>
      <scope>shared</scope>
    </file_system>
    ```
"""
        elif sub_action == "replace_lines":
            return common_header + """
**Action: replace_lines**
Replaces a specific block of existing lines with new content. STRONGLY RECOMMENDED for modifying existing files.
*   `<filename>` (string, required): Relative path to the file.
*   `<replace_start_line>` (integer, required): The first line number (1-indexed) to remove. Also accepts `<start_line>`.
*   `<replace_end_line>` (integer, required): The last line number (1-indexed) to remove (inclusive). Also accepts `<end_line>`.
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
*   `<find_text>` (string, required): Exact string to find. (Can also use `<find>` or `<search>`)
*   `<replace_text>` (string, required): Replacement string. (Can also use `<replace>`)
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
        elif sub_action == "search_replace_block":
            return common_header + """
**Action: search_replace_block**
Finds a specific block of code/text and replaces it with new content. Uses a 3-tier matching strategy:
1. **Exact match** — if the `search_block` appears exactly once, it is replaced.
2. **First/last line match** — if exact match fails, the first and last non-empty lines of `search_block` are used to locate the block in the file. Useful when indentation or internal whitespace may differ slightly.
3. **Fuzzy match** — `diff_match_patch` fallback for slight variations.

**Parameters:**
*   `<filename>` (string, required): Relative path to the file.
*   `<search_block>` (string, required): The block of text to find. Provide at minimum the **first and last unique lines** of the block. (Can also use `<search>` or `<find>`)
*   `<replace_block>` (string, required): The new content to replace the found block with. (Can also use `<replace_text>`)
*   `<expected_replacements>` (integer, optional): **Required for multi-match cases.** If the tool reports it found N matches, re-send with `<expected_replacements>N</expected_replacements>` to confirm replacing all N occurrences.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_init":
            return common_header + """
**Action: git_init**
Initializes a new git repository in the specified path.
*   `<path>` (string, optional): The directory to run git init in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_commit":
            return common_header + """
**Action: git_commit**
Commits changes in the specified scope (if it is a git repository).
*   `<commit_message>` (string, required): The message for the commit.
*   `<path>` (string, optional): The directory to run the commit in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_status":
            return common_header + """
**Action: git_status**
Gets the git status for the specified scope (if it is a git repository).
*   `<path>` (string, optional): The directory to run git status in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_diff":
            return common_header + """
**Action: git_diff**
Gets the git diff for the specified scope (if it is a git repository).
*   `<path>` (string, optional): The directory to run git diff in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""

        elif sub_action == "git_log":
            return common_header + """
**Action: git_log**
Gets the git commit history for the specified scope.
*   `<path>` (string, optional): The directory to run git log in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_branch":
            return common_header + """
**Action: git_branch**
Lists branches or creates a new one if `<branch>` is provided.
*   `<branch>` (string, optional): Name of the new branch to create.
*   `<path>` (string, optional): The directory to run git branch in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_checkout":
            return common_header + """
**Action: git_checkout**
Switches to a specified branch.
*   `<branch>` (string, required): Name of the branch to checkout.
*   `<path>` (string, optional): The directory to run git checkout in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_add":
            return common_header + """
**Action: git_add**
Stages files for commit.
*   `<files>` (string, required): Comma-separated list of files, or '.' for all.
*   `<path>` (string, optional): The directory to run git add in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_pull":
            return common_header + """
**Action: git_pull**
Pulls changes from a remote repository.
*   `<remote>` (string, optional): The remote to pull from (default: 'origin').
*   `<path>` (string, optional): The directory to run git pull in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""
        elif sub_action == "git_push":
            return common_header + """
**Action: git_push**
Pushes changes to a remote repository.
*   `<remote>` (string, optional): The remote to push to (default: 'origin').
*   `<path>` (string, optional): The directory to run git push in. Defaults to root of scope.
*   `<scope>` (string, optional): 'private' or 'shared'. Default: 'private'.
"""

        # Return directory of commands
        return common_header + """
**Available Actions Summary:**
1.  **read:** Reads file content. (Use start_line and end_line for large files).
2.  **write:** Overwrites a file completely.
3.  **append:** Adds text to the end of a file.
4.  **insert_lines:** Inserts a block of text at a specific line number, or relative to a search anchor.
5.  **replace_lines:** Replaces a block of lines with new text.
6.  **list:** Lists files in a directory.
7.  **mkdir:** Creates a directory.
8.  **delete:** Deletes a file or empty directory.
9.  **find_replace:** Exact string replacement.
10. **regex_replace:** Regex based string replacement.
11. **copy:** Copies files/directories.
12. **move:** Moves/renames files/directories.
13. **search_replace_block:** Fuzzy search and replace code blocks.
14. **git_commit:** Commits modifications via Git.
15. **git_status:** Gets repository status.
16. **git_diff:** Gets undiscarded modifications.
17. **git_log:** Gets repository commit history.
18. **git_branch:** Lists or creates branches.
19. **git_checkout:** Switches branches.
20. **git_add:** Stages files.
21. **git_pull:** Pulls from remote.
22. **git_push:** Pushes to remote.
23. **git_init:** Initializes a new git repository.

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
        
        # Sanitize the path: strip spaces and replace internal spaces with underscores
        if isinstance(relative_file_path, str):
            relative_file_path = relative_file_path.strip().replace(" ", "_")
            
            # Agent pathing workaround: LLMs often mistakenly prepend "shared/" or "shared/project_name/" 
            # to their paths when working in the shared workspace because of prompt wording.
            if "shared workspace" in scope_description:
                parts = tuple(Path(relative_file_path).parts)
                if parts and parts[0] == "shared":
                    parts = parts[1:] # Strip 'shared/'
                    
                # See if they also included the project name (which is the parent of the parent of the shared_workspace)
                # base_path is: .../projects/ProjectName/SessionName/shared_workspace
                if parts and len(base_path.parents) >= 2 and parts[0] == base_path.parent.parent.name:
                    parts = parts[1:] # Strip 'ProjectName/'

                # See if they included the session name (which is the parent of the shared_workspace)
                if parts and len(base_path.parents) >= 1 and parts[0] == base_path.parent.name:
                    parts = parts[1:] # Strip 'SessionName/'

                relative_file_path = str(Path(*parts)) if parts else "."
            
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
        
        # Prevent overwriting existing files to force surgical edits
        if validated_path.exists():
            logger.warning(f"Agent {agent_id} attempted to overwrite existing file '{filename}'. Write rejected to strongly encourage find/replace targeted edits.")
            return {
                "status": "error", 
                "message": f"File '{filename}' already exists. Overwriting entire existing files using 'write' is disabled to save tokens and prevent accidental losses. Please use 'read' to view the file and then use 'find_replace', 'regex_replace', 'insert_lines', or 'append' to modify specific sections. If you must recreate the file from scratch, use 'delete' first."
            }
            
        try:
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            logger.info(f"Agent {agent_id} successfully wrote file: '{filename}' to {scope_description}")
            return {"status": "success", "message": f"Successfully wrote content to '{filename}' in {scope_description}."}
        except FileExistsError:
            logger.warning(f"Agent {agent_id} attempted to write file '{filename}' but a parent path exists as a file."); return {"status": "error", "message": f"Cannot write to '{filename}': A parent path already exists but is a FILE, not a directory. Did you previously use 'write' instead of 'mkdir' to create a directory?"}
        except NotADirectoryError:
            logger.warning(f"Agent {agent_id} attempted to write file '{filename}' but a parent path is not a directory."); return {"status": "error", "message": f"Cannot write to '{filename}': A parent path already exists but is a FILE, not a directory. Did you previously use 'write' instead of 'mkdir' to create a directory?"}
        except PermissionError: logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when writing to file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error writing file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error writing file '{filename}': {type(e).__name__} - {e}"}


    async def _list_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Lists files and directories within a specified sub-directory of the base path."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_dir}' in {scope_description}."}
        if not validated_path.is_dir():
            logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir} in {scope_description}")
            # Instead of erroring (which triggers retries), return helpful info with root listing
            try:
                root_items = await asyncio.to_thread(os.listdir, base_path)
                root_listing = "\n".join([f"- {item}" for item in sorted(root_items)]) if root_items else "(empty workspace)"
            except Exception:
                root_listing = "(could not list root)"
            return {
                "status": "success",
                "message": f"Path '{relative_dir}' does not exist yet in {scope_description}. You may need to create it first using 'mkdir'. Here are the current contents of the workspace root:\n{root_listing}",
                "items": []
            }
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

    async def _resolve_insert_line_from_search(self, base_path: Path, filename: str, search_text: str, agent_id: str, scope_description: str, position_hint: str = "after") -> Optional[int]:
        """Resolve a line number by searching for an anchor string in the file."""
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path or not val_path.is_file(): return None
        try:
            lines = val_path.read_text(encoding='utf-8').splitlines()
            for i, line in enumerate(lines):
                if search_text in line:
                    if position_hint and position_hint.lower() in ("before",):
                        return i + 1  # Insert before the matched line (1-indexed)
                    else:
                        return i + 2  # Insert after the matched line (1-indexed)
            return None
        except Exception:
            return None

    async def _insert_lines_in_file(self, base_path: Path, filename: str, insert_line: int, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        # Defensive: ensure insert_line is always int even if caller passes a string
        try:
            insert_line = int(insert_line)
        except (ValueError, TypeError):
            return {"status": "error", "message": f"'insert_line' must be an integer line number, got '{insert_line}'."}
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

    async def _search_replace_block_in_file(self, base_path: Path, filename: str, search_block: Optional[str], replace_block: str, agent_id: str, scope_description: str, expected_replacements: Optional[int] = None, start_marker: Optional[str] = None, end_marker: Optional[str] = None) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        try:
            def search_replace_sync():
                original_content = val_path.read_text(encoding='utf-8')

                if start_marker and end_marker:
                    start_idx = original_content.find(start_marker)
                    if start_idx == -1: return False, f"start_marker '{start_marker}' not found in the file."
                    end_idx = original_content.find(end_marker, start_idx + len(start_marker))
                    if end_idx == -1: return False, f"end_marker '{end_marker}' not found after the start_marker."
                    
                    old_block = original_content[start_idx:end_idx + len(end_marker)]
                    exact_count = original_content.count(old_block)
                    if exact_count == 1:
                        new_content = original_content.replace(old_block, replace_block, 1)
                        val_path.write_text(new_content, encoding='utf-8')
                        return True, "Marker block found and replaced successfully."
                    elif exact_count > 1:
                        if expected_replacements is not None and expected_replacements == exact_count:
                            new_content = original_content.replace(old_block, replace_block)
                            val_path.write_text(new_content, encoding='utf-8')
                            return True, f"Replaced all {exact_count} identical marker blocks."
                        return False, f"Found {exact_count} identical marker blocks. Re-send with expected_replacements={exact_count} to confirm."
                    return False, "Failed to extract old block using markers."

                if search_block is None:
                    return False, "Unexpected: search_block is None but markers were not used."

                # ── TIER 1: Exact substring match ─────────────────────────────────
                exact_count = original_content.count(search_block)
                if exact_count == 1:
                    new_content = original_content.replace(search_block, replace_block, 1)
                    val_path.write_text(new_content, encoding='utf-8')
                    return True, "Exact match found and replaced."

                if exact_count > 1:
                    if expected_replacements is not None and expected_replacements == exact_count:
                        new_content = original_content.replace(search_block, replace_block)
                        val_path.write_text(new_content, encoding='utf-8')
                        return True, f"Replaced all {exact_count} exact occurrences (confirmed by expected_replacements)."
                    return False, (
                        f"Found {exact_count} exact matches. To replace ALL {exact_count} occurrences, "
                        f"re-send this call with <expected_replacements>{exact_count}</expected_replacements>. "
                        f"To replace only ONE, provide a more specific search_block that is unique in the file."
                    )

                    # ── TIER 2: First/last line matching ──────────────────────────────
                # Extract first and last non-empty lines of the search block.
                # This lets the LLM provide just the anchor lines when the full block
                # is hard to reproduce exactly, saving tokens.
                search_lines = [l for l in search_block.splitlines() if l.strip()]
                if len(search_lines) >= 2:
                    first_line = search_lines[0]
                    last_line = search_lines[-1]
                    file_lines = original_content.splitlines(keepends=True)

                    # Find all start indices in file_lines where first_line occurs
                    start_indices = [
                        i for i, fl in enumerate(file_lines)
                        if fl.rstrip('\n\r') == first_line or fl.strip() == first_line.strip()
                    ]

                    matched_spans = []
                    for start_idx in start_indices:
                        # From start_idx, search forward for the last_line
                        for end_idx in range(start_idx, min(start_idx + len(search_lines) * 3, len(file_lines))):
                            if file_lines[end_idx].rstrip('\n\r') == last_line or file_lines[end_idx].strip() == last_line.strip():
                                matched_spans.append((start_idx, end_idx))
                                break  # Take the shortest matching span from this start

                    if len(matched_spans) == 1:
                        start_idx, end_idx = matched_spans[0]
                        before = "".join(file_lines[:start_idx])
                        after = "".join(file_lines[end_idx + 1:])
                        # Preserve trailing newline convention of the replaced block
                        replacement = replace_block
                        if not replacement.endswith('\n') and after:
                            replacement += '\n'
                        new_content = before + replacement + after
                        val_path.write_text(new_content, encoding='utf-8')
                        return True, (
                            f"First/last line match found (lines {start_idx+1}–{end_idx+1}) and replaced."
                        )

                    if len(matched_spans) > 1:
                        n = len(matched_spans)
                        if expected_replacements is not None and expected_replacements == n:
                            # Replace all spans in reverse order to keep indices valid
                            file_lines_mut = list(file_lines)
                            for start_idx, end_idx in reversed(matched_spans):
                                replacement = replace_block
                                if not replacement.endswith('\n'):
                                    replacement += '\n'
                                file_lines_mut[start_idx:end_idx + 1] = [replacement]
                            val_path.write_text("".join(file_lines_mut), encoding='utf-8')
                            return True, f"Replaced all {n} first/last-line matches (confirmed by expected_replacements)."
                        return False, (
                            f"First/last line anchor matched {n} distinct blocks. "
                            f"To replace ALL {n}, re-send with <expected_replacements>{n}</expected_replacements>. "
                            f"To replace only one, provide a more specific search_block with unique surrounding lines."
                        )

                # ── TIER 3: Fuzzy fallback (diff_match_patch) ─────────────────────
                dmp = diff_match_patch()
                dmp.Match_Distance = len(original_content) * 10
                
                idx = dmp.match_main(original_content, search_block, 0)
                patches = dmp.patch_make(search_block, replace_block)
                if idx != -1:
                    for p in patches:
                        p.start1 += idx
                        p.start2 += idx

                new_content, results = dmp.patch_apply(patches, original_content)

                if not any(results):
                    # Provide useful context: show first/last line of search_block
                    preview_first = search_lines[0][:80] if search_lines else "(empty)"
                    preview_last  = search_lines[-1][:80] if len(search_lines) > 1 else ""
                    hint = (
                        f"Could not find a matching block. "
                        f"Searched for block starting with: {repr(preview_first)}"
                        + (f" and ending with: {repr(preview_last)}" if preview_last else "") +
                        ". Please read the file and verify the exact content before retrying. Consider using the 'replace_lines' action instead, which is much more reliable if you know the exact line numbers."
                    )
                    return False, hint

                val_path.write_text(new_content, encoding='utf-8')
                return True, "Fuzzy match found and replaced."

            success, msg = await asyncio.to_thread(search_replace_sync)

            if success:
                logger.info(f"Agent {agent_id} search/replace block in '{filename}' ({scope_description}): {msg}")
                return {"status": "success", "message": f"Successfully replaced block in '{filename}'. ({msg})"}
            else:
                logger.warning(f"Agent {agent_id} failed search/replace block in '{filename}' ({scope_description}): {msg}")
                return {"status": "error", "message": msg}
        except Exception as e:
            logger.error(f"Agent {agent_id} error replacing block in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error replacing block in '{filename}': {e}"}

    async def _git_commit(self, base_path: Path, relative_path: str, commit_message: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_commit_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                repo.git.add(all=True)
                repo.index.commit(commit_message)
                return repo.head.commit.hexsha
            
            commit_sha = await asyncio.to_thread(git_commit_sync)
            logger.info(f"Agent {agent_id} created git commit {commit_sha[:7]} in {scope_description}")
            return {"status": "success", "message": f"Successfully created git commit: {commit_sha[:7]} - {commit_message}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git command failed: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error committing in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error committing: {e}"}

    async def _git_status(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_status_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.status()
            
            status_output = await asyncio.to_thread(git_status_sync)
            return {"status": "success", "message": f"Git status:\n{status_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git status in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git status: {e}"}

    async def _git_diff(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_diff_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.diff()
            
            diff_output = await asyncio.to_thread(git_diff_sync)
            if not diff_output:
                diff_output = "No current modifications."
            return {"status": "success", "message": f"Git diff:\n{diff_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git diff in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git diff: {e}"}

    async def _git_log(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_log_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.log('-n', '10', '--oneline') # Limit to 10 for token safety
            
            log_output = await asyncio.to_thread(git_log_sync)
            return {"status": "success", "message": f"Git log (last 10):\n{log_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error getting log: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git log in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git log: {e}"}

    async def _git_branch(self, base_path: Path, relative_path: str, branch: Optional[str], agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_branch_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                if branch:
                    repo.git.branch(branch)
                    return f"Created branch '{branch}'"
                else:
                    return repo.git.branch()
            
            result_output = await asyncio.to_thread(git_branch_sync)
            return {"status": "success", "message": f"Git branch result:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on branch: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error executing git branch in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing git branch: {e}"}

    async def _git_checkout(self, base_path: Path, relative_path: str, branch: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_checkout_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                repo.git.checkout(branch)
                return f"Switched or checked out to '{branch}'"
            
            result_output = await asyncio.to_thread(git_checkout_sync)
            return {"status": "success", "message": result_output}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on checkout: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error checking out branch in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error checking out branch: {e}"}

    async def _git_add(self, base_path: Path, relative_path: str, files_to_add: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_add_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                # Split comma separated files
                files_list = [f.strip() for f in files_to_add.split(',') if f.strip()]
                repo.git.add(*files_list)
                return f"Staged: {', '.join(files_list)}"
            
            result_output = await asyncio.to_thread(git_add_sync)
            return {"status": "success", "message": result_output}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on add: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error staging files in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error staging files: {e}"}

    async def _git_init(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_init_sync():
                repo = git.Repo.init(val_path)
                return "Initialized empty Git repository"
            
            result_output = await asyncio.to_thread(git_init_sync)
            logger.info(f"Agent {agent_id} initialized git repo in '{relative_path}' ({scope_description})")
            return {"status": "success", "message": result_output}
        except Exception as e:
            logger.error(f"Agent {agent_id} error initializing git repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error initializing git repo: {e}"}

    async def _git_pull(self, base_path: Path, relative_path: str, remote: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_pull_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.pull(remote)
            
            result_output = await asyncio.to_thread(git_pull_sync)
            return {"status": "success", "message": f"Git pull successful:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error pulling repository: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error pulling repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error pulling repo: {e}"}

    async def _git_push(self, base_path: Path, relative_path: str, remote: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_push_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.push(remote)
            
            result_output = await asyncio.to_thread(git_push_sync)
            return {"status": "success", "message": f"Git push successful:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error pushing repository: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error pushing repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error pushing repo: {e}"}
