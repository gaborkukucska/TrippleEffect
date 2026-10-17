import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import traceback
import json
import re

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings
from diff_match_patch import diff_match_patch

logger = logging.getLogger(__name__)

class CodeEditorTool(BaseTool):
    """
    Tool for safe, diff-based file editing using standard search/replace or patch functionality.
    Restricted to sandbox and project directories.
    """
    name: str = "code_editor"
    auth_level: str = "worker"
    summary: Optional[str] = "Safe code editing using multiple replace chunks or diffs."
    description: str = (
        "Advanced code editing capabilities for precise modifications. "
        "Allows modifying multiple non-contiguous segments of code in a single file safely. "
        "Actions: 'replace_chunks'. Note: Each chunk's 'search' + 'replace' text is strictly limited to a combined 16384 characters. If editing massive files, split into multiple chunks or calls."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'replace_chunks'.",
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Target scope: 'private', 'shared', or 'projects'. Defaults to 'shared'.",
            required=False,
        ),
        ToolParameter(
            name="filename",
            type="string",
            description="Relative path to the file within the specified scope.",
            required=True,
            aliases=["filepath", "file", "path", "file_path"]
        ),
        ToolParameter(
            name="chunks",
            type="array",
            description="An array of objects. Each object MUST have a 'replace' string. To target lines, provide 'start_line' and 'end_line' integers. To target text, provide a 'search' string.",
            required=True,
            aliases=["replacements", "replace_chunks", "edits", "replacements_json", "modifications"]
        )
    ]

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        
        action = kwargs.get("action")
        # Handle action aliases
        if action in ["replace", "edit", "modify", "replace_chunk"]:
            action = "replace_chunks"
            
        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()
        filename = kwargs.get("filename") or kwargs.get("filepath") or kwargs.get("file") or kwargs.get("path") or kwargs.get("file_path")
        if isinstance(filename, str):
            filename = filename.strip()

        # Gracefully handle write_file called on code_editor
        if action in ["write_file", "write", "create_file"]:
            file_content = kwargs.get("file_content") or kwargs.get("content")
            if file_content is not None and filename:
                logger.info(f"CodeEditorTool: Handled '{action}' alias for '{filename}' directly.")
                from src.tools.file_system import FileSystemTool
                return await FileSystemTool().execute(
                    agent_id=agent_id, agent_sandbox_path=agent_sandbox_path,
                    project_name=project_name, session_name=session_name,
                    action="write", path=filename, content=str(file_content)
                )

        if action != "replace_chunks":
            return {"status": "error", "message": f"Action '{action}' is not supported. Use 'replace_chunks'."}

        chunks = kwargs.get("chunks") or kwargs.get("replacements") or kwargs.get("edits")
        if not filename or not chunks:
            return {"status": "error", "message": "Missing 'filename' or 'chunks' parameter."}
            
        if isinstance(chunks, str):
            try:
                # Round 1v: strict=False accepts raw line breaks/tabs inside the JSON strings (the 4B model sends code that
                # way: 6 rejections "Invalid control character" on 2026-10-16). Valid JSON parses exactly as before.
                chunks = json.loads(chunks, strict=False)
            except Exception as e1:
                # JSON Resilience Layer
                try:
                    # Try to fix common JSON errors (single quotes, trailing commas, missing closing brackets)
                    fixed_str = chunks.strip()
                    # Remove markdown formatting if present
                    if fixed_str.startswith("```json"):
                        fixed_str = fixed_str[7:]
                    elif fixed_str.startswith("```"):
                        fixed_str = fixed_str[3:]
                    if fixed_str.endswith("```"):
                        fixed_str = fixed_str[:-3]
                    
                    # Fix single quotes to double quotes, being careful around escaped quotes
                    # This is a naive regex but helps with simple LLM errors
                    fixed_str = re.sub(r"(?<!\\)'", '"', fixed_str)
                    # Fix trailing commas
                    fixed_str = re.sub(r',\s*([}\]])', r'\1', fixed_str)
                    
                    chunks = json.loads(fixed_str)
                except Exception as e2:
                    try:
                        import ast
                        # AST fallback for single-quote JSON
                        parsed = ast.literal_eval(chunks.strip())
                        if isinstance(parsed, list):
                            chunks = parsed
                        else:
                            raise ValueError("AST evaluated to non-list")
                    except Exception as e3:
                        return {"status": "error", "message": f"'chunks' was provided as a string but is not valid JSON. Ensure you use an array of objects. Parse error: {e1}. Repair attempt failed: {e3}"}
                
        if not isinstance(chunks, list):
            return {"status": "error", "message": "'chunks' must be a list of dicts with 'search' and 'replace' keys."}

        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                return {"status": "error", "message": f"Chunk at index {idx} is invalid (got {type(chunk).__name__}). 'chunks' must be a list of JSON objects containing 'search' and 'replace' keys."}

        # --- FIX +42: Guard against oversized chunks that cause LLM JSON overflow ---
        MAX_CHUNKS = 20
        MAX_CHUNK_SIZE_CHARS = 32768  # 32KB per individual chunk (increased from 16384)
        
        if len(chunks) > MAX_CHUNKS:
            return {"status": "error", "message": f"Too many chunks ({len(chunks)}). Maximum is {MAX_CHUNKS}. Please split your edits into multiple smaller tool calls."}
        
        for idx, chunk in enumerate(chunks):
            if isinstance(chunk, dict):
                search_len = len(str(chunk.get('search', '')))
                replace_len = len(str(chunk.get('replace', '')))
                if search_len + replace_len > MAX_CHUNK_SIZE_CHARS:
                    # Auto-split oversized chunk logic is too complex for this tool's exact search match, 
                    # so we instruct the LLM to do it instead.
                    return {"status": "error", "message": f"Chunk {idx} is too large ({search_len + replace_len} chars, max {MAX_CHUNK_SIZE_CHARS}). Split this edit into multiple smaller chunks by separating your changes into distinct search/replace blocks."}

        # --- Base path logic identical to file_system ---
        base_path: Optional[Path] = None
        scope_description = ""
        if scope == "private":
            base_path = agent_sandbox_path; scope_description = f"agent {agent_id}'s private sandbox"
            if not base_path.is_dir(): return {"status": "error", "message": f"Sandbox does not exist: {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Missing project/session context."}
            safe_project_name = project_name.replace(" ", "_").strip()
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"
            scope_description = f"shared workspace"
            if not base_path.is_dir():
                try: 
                    await asyncio.to_thread(base_path.mkdir, parents=True, exist_ok=True)
                except Exception as e: return {"status": "error", "message": f"Could not create shared workspace: {e}"}
        elif scope == "projects":
            base_path = settings.PROJECTS_BASE_DIR
            scope_description = "main projects directory"
            if not base_path.is_dir(): return {"status": "error", "message": "Projects dir does not exist."}
        else:
            return {"status": "error", "message": "Invalid scope. Use 'private', 'shared', or 'projects'."}

        try:
            rel_file_path = filename.lstrip('/') or '.'
            if '..' in Path(rel_file_path).parts:
                return {"status": "error", "message": "Unsafe path containing '..'"}
            abs_path = (base_path / Path(rel_file_path)).resolve()
            
            # Additional code_editor constraint: Can only edit files in the allowed workspaces.
            try:
                abs_path.relative_to(base_path.resolve())
            except ValueError:
                return {"status": "error", "message": f"Security restriction: The file at {abs_path} is outside the allowed {scope} path."}

            if not abs_path.is_file():
                return {"status": "error", "message": f"File '{filename}' does not exist or is a directory."}
                
            original_content = await asyncio.to_thread(abs_path.read_text, encoding="utf-8")
            
            # Apply all chunks
            content = original_content
            successful = 0
            errors = []
            
            # Sort chunks to prevent line shifting collisions
            # Process line-based edits from bottom to top
            def get_start_line(c):
                sl = c.get('start_line') or c.get('from_line') or c.get('line_start')
                try: return int(sl) if sl is not None else -1
                except: return -1
            
            # Use stable sort: line-based chunks descending, search chunks stay in relative order
            chunks = sorted(chunks, key=get_start_line, reverse=True)
            
            for idx, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    errors.append(f"Chunk {idx} is malformed (expected a JSON object).")
                    continue
                    
                # Support semantic aliases for replace text
                replace_text = (
                    chunk.get('replace') if 'replace' in chunk else
                    chunk.get('replace_block') if 'replace_block' in chunk else
                    chunk.get('replace_text') if 'replace_text' in chunk else
                    chunk.get('replacement') if 'replacement' in chunk else
                    chunk.get('new') if 'new' in chunk else
                    chunk.get('new_code')
                )
                if replace_text is None:
                    # Round 1x: say how to delete lines; W1 sent {'start_line': 31} chunks without 'replace' four times.
                    errors.append(f"Chunk {idx} is malformed (missing 'replace' key). Every chunk needs 'replace' with the new "
                                  "text; to DELETE lines give start_line, end_line and \"replace\": \"\".")
                    continue
                    
                start_line = chunk.get('start_line') or chunk.get('from_line') or chunk.get('line_start') or chunk.get('start')
                end_line = chunk.get('end_line') or chunk.get('to_line') or chunk.get('line_end') or chunk.get('end')
                search_text = (
                    chunk.get('search') if 'search' in chunk else
                    chunk.get('search_block') if 'search_block' in chunk else
                    chunk.get('search_text') if 'search_text' in chunk else
                    chunk.get('find') if 'find' in chunk else
                    chunk.get('old') if 'old' in chunk else
                    chunk.get('old_code')
                )
                if isinstance(search_text, str) and not search_text.strip():
                    # Round 1x: an empty 'search' matched "987 times" and was rejected as ambiguous. It means "no search".
                    search_text = None

                # Single-line inference if only one bound is provided
                if start_line is not None and end_line is None and search_text is None:
                    end_line = start_line
                elif end_line is not None and start_line is None and search_text is None:
                    start_line = end_line
                
                if start_line is not None and end_line is not None:
                    try:
                        start_line = int(start_line)
                        end_line = int(end_line)
                    except ValueError:
                        errors.append(f"Chunk {idx}: start_line and end_line must be integers.")
                        continue
                        
                    lines = content.splitlines(True)
                    start_idx = max(0, start_line - 1)
                    end_idx = min(len(lines), end_line)
                    
                    if start_idx >= len(lines) or start_idx > end_idx:
                        errors.append(f"Chunk {idx}: Invalid start_line/end_line range for file with {len(lines)} lines.")
                        continue
                        
                    replacement = replace_text
                    # Round 1x: keep the line break the replaced lines had. "replace": "" now really deletes the lines (it
                    # used to leave an empty line), and replacing the last line no longer drops the file's final newline.
                    replaced_ends_with_newline = lines[end_idx - 1].endswith('\n') if end_idx > start_idx else end_idx < len(lines)
                    if replacement and not replacement.endswith('\n') and replaced_ends_with_newline:
                        replacement += '\n'
                        
                    before = "".join(lines[:start_idx])
                    after = "".join(lines[end_idx:])
                    content = before + replacement + after
                    successful += 1
                    continue
                elif search_text is not None:
                    occurrences = content.count(search_text)
                    if occurrences == 0:
                        # Fallback to AST literal_eval if json.loads fails
                        # Try regex match ignoring all whitespace
                        pieces = [re.escape(p) for p in search_text.split() if p]
                        if not pieces:
                            errors.append(f"Chunk {idx}: Search text is empty or only whitespace.")
                            continue
                            
                        regex_pattern = r'\s*'.join(pieces)
                        # allow agents to use ... to skip lines
                        regex_pattern = regex_pattern.replace(re.escape('...'), r'[\s\S]*?')
                        matches = list(re.finditer(regex_pattern, content))
                        
                        if len(matches) == 1:
                            match = matches[0]
                            before = content[:match.start()]
                            after = content[match.end():]
                            
                            replacement = replace_text
                            if not replacement.endswith('\n') and after and not after.startswith('\n'):
                                replacement += '\n'
                                
                            content = before + replacement + after
                            successful += 1
                            continue
                        elif len(matches) > 1:
                            errors.append(f"Chunk {idx}: Search text is ambiguous (found {len(matches)} times ignoring whitespace).")
                            continue
                        else:
                            # Try robust patch-based mechanism as last resort
                            try:
                                dmp = diff_match_patch()
                                patches = dmp.patch_make(search_text, replace_text)
                                new_content, results = dmp.patch_apply(patches, content)
                                if len(results) > 0 and all(results):
                                    content = new_content
                                    successful += 1
                                    continue
                            except Exception as e:
                                logger.debug(f"dmp patch failed: {e}")
                                
                        # Provide fuzzy match attempt context
                        import difflib
                        search_lines = search_text.splitlines()
                        content_lines = content.splitlines()
                        if search_lines and content_lines:
                            first_line = next((line for line in search_lines if line.strip()), None)
                            if first_line:
                                close_matches = difflib.get_close_matches(first_line, content_lines, n=1, cutoff=0.6)
                                if close_matches:
                                    best_line = close_matches[0]
                                    line_idx = content_lines.index(best_line)
                                    start_idx = max(0, line_idx - 2)
                                    end_idx = min(len(content_lines), line_idx + len(search_lines) + 2)
                                    context = '\n'.join(content_lines[start_idx:end_idx])
                                    errors.append(f"Chunk {idx}: Search text not found exactly. Found a close match here:\n```\n{context}\n```\nCRITICAL: If the block above is the section you want to edit, you MUST copy it EXACTLY including all indentation and spacing. If it is NOT the correct section, you MUST use the `file_system` tool with `action='read'` to review the file contents to find the correct exact text before trying to edit again.")
                                    continue
                                    
                        errors.append(f"Chunk {idx}: Search text not found in file. Ensure exact matching. Use the `file_system` tool with `action='read'` to review the file contents to find the correct exact text before trying again.")
                    elif occurrences > 1:
                        errors.append(f"Chunk {idx}: Search text is ambiguous (found {occurrences} times). Please make the search block larger/more unique, or use start_line and end_line.")
                    else:
                        content = content.replace(search_text, replace_text)
                        successful += 1
                else:
                    errors.append(f"Chunk {idx} is malformed: Provide either 'start_line'/'end_line' OR 'search'.")
                    continue
            
            if errors:
                return {
                    "status": "error",
                    "message": f"Code edit failed: {successful} chunks succeeded, but {len(errors)} failed issues occurred.\n" + "\n".join(errors) + "\nNo changes were saved."
                }
                
            # Perform basic Python syntax validation if modifying a python file
            if filename.endswith('.py'):
                try:
                    import ast
                    ast.parse(content)
                except SyntaxError as syntax_err:
                    try:  # Round 1v: same code-around-the-error report as file_system writes
                        from src.tools.python_static_check import syntax_error_report
                        syntax_report = syntax_error_report(content, syntax_err)
                    except Exception:
                        syntax_report = ""
                    return {
                        "status": "error",
                        "message": f"Code edit rejected due to Python SyntaxError in resulting code: {syntax_err.msg} at line {syntax_err.lineno}. No changes were saved. Please check your indentation and brackets."
                                   + (f"\n{syntax_report}" if syntax_report else "")
                    }
            
            # Save if all success
            await asyncio.to_thread(abs_path.write_text, content, encoding="utf-8")
            return {
                "status": "success",
                "message": f"Successfully applied {successful} edits to '{filename}'."
            }

        except Exception as e:
            logger.error(f"Error in code_editor tool: {e}\n{traceback.format_exc()}")
            return {"status": "error", "message": f"Unhandled error during edit: {str(e)}"}
            
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** code_editor

        **Description:** Safely applies multiple targeted substring replacements in a single operation. This avoids having to call `file_system` multiple times for different blocks.

        **Parameters:**
        *   **action:** (string, required) - Must be 'replace_chunks'.
        *   **filename:** (string, required) - Relative path to the file.
        *   **chunks:** (array, required) - Array of objects containing 'replace' and either 'start_line' & 'end_line' (preferred) or 'search' (fallback).
        *   **scope:** (string, optional) - Target scope ('private', 'shared', or 'projects').

        **Example:**
        ```json
        {
            "action": "replace_chunks",
            "filename": "src/main.py",
            "chunks": [
                {
                    "search": "def old_function():\n    print('A')",
                    "replace": "def new_function():\n    print('B')"
                },
                {
                    "search": "x = 10",
                    "replace": "x = 20"
                }
            ]
        }
        ```
        Important Notes:
        - The `search` block must match existing content EXACTLY ONCE (including all whitespace, newlines, and indentation).
        - If you encounter a 'Search text not found exactly' error, DO NOT guess the whitespace. Use the `file_system` tool with `action='read'` to view the file contents and copy the EXACT text before retrying.
        - If a search string appears multiple times or zero times, the entire tool call is rejected.
        """
        return usage.strip()
