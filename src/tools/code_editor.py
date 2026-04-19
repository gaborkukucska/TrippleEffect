import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import traceback

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
        "Actions: 'replace_chunks'."
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
            aliases=["filepath", "file"]
        ),
        ToolParameter(
            name="chunks",
            type="array",
            description="An array of objects, where each object has 'search' and 'replace' string keys. The tool will find exact matches for 'search' and replace them with 'replace'.",
            required=True,
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
        if action != "replace_chunks":
            return {"status": "error", "message": "Only 'replace_chunks' action is supported."}
            
        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()
        filename = kwargs.get("filename")
        if isinstance(filename, str):
            filename = filename.strip()
            
        chunks = kwargs.get("chunks")
        if not filename or not chunks:
            return {"status": "error", "message": "Missing 'filename' or 'chunks' parameter."}
            
        if not isinstance(chunks, list):
            return {"status": "error", "message": "'chunks' must be a list of dicts with 'search' and 'replace' keys."}

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

        # Validate Path
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
            
            for idx, chunk in enumerate(chunks):
                if not isinstance(chunk, dict) or 'search' not in chunk or 'replace' not in chunk:
                    errors.append(f"Chunk {idx} is malformed (missing 'search' or 'replace' key).")
                    continue
                    
                search_text = chunk['search']
                replace_text = chunk['replace']
                
                occurrences = content.count(search_text)
                if occurrences == 0:
                    # Tier 2: First/last line match fallback (handles indentation/whitespace hallucination)
                    search_lines = [l for l in search_text.splitlines() if l.strip()]
                    if len(search_lines) >= 2:
                        first_line = search_lines[0]
                        last_line = search_lines[-1]
                        file_lines = content.splitlines(keepends=True)
                        
                        start_indices = [
                            i for i, fl in enumerate(file_lines)
                            if fl.rstrip('\n\r') == first_line or fl.strip() == first_line.strip()
                        ]
                        
                        matched_spans = []
                        for start_idx in start_indices:
                            for end_idx in range(start_idx, min(start_idx + len(search_lines) * 3, len(file_lines))):
                                if file_lines[end_idx].rstrip('\n\r') == last_line or file_lines[end_idx].strip() == last_line.strip():
                                    matched_spans.append((start_idx, end_idx))
                                    break
                                    
                        if len(matched_spans) == 1:
                            start_idx, end_idx = matched_spans[0]
                            before = "".join(file_lines[:start_idx])
                            after = "".join(file_lines[end_idx + 1:])
                            replacement = replace_text
                            if not replacement.endswith('\n') and after:
                                replacement += '\n'
                            content = before + replacement + after
                            successful += 1
                            continue
                        elif len(matched_spans) > 1:
                            errors.append(f"Chunk {idx}: Search text not found exactly, and first/last line anchor is ambiguous ({len(matched_spans)} matches).")
                            continue
                            
                    # Provide fuzzy match attempt context
                    errors.append(f"Chunk {idx}: Search text not found in file. Ensure exact matching.")
                elif occurrences > 1:
                    errors.append(f"Chunk {idx}: Search text is ambiguous (found {occurrences} times). Please make the search block larger/more unique.")
                else:
                    content = content.replace(search_text, replace_text)
                    successful += 1
            
            if errors:
                return {
                    "status": "error",
                    "message": f"Code edit failed: {successful} chunks succeeded, but {len(errors)} failed issues occurred.\n" + "\n".join(errors) + "\nNo changes were saved."
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
        *   **chunks:** (array, required) - Array of objects containing 'search' and 'replace' keys.
        *   **scope:** (string, optional) - Target scope ('private', 'shared', or 'projects').

        **Example:**
        ```json
        {
            "action": "replace_chunks",
            "filename": "src/main.py",
            "chunks": [
                {
                    "search": "def old_function():\\n    print('A')",
                    "replace": "def new_function():\\n    print('B')"
                },
                {
                    "search": "x = 10",
                    "replace": "x = 20"
                }
            ]
        }
        ```
        Important Notes:
        - The `search` block must exactly match existing content (including whitespace/indentation) exactly ONCE.
        - If a search string appears multiple times or zero times, the entire tool call is atomically rejected and no changes are written.
        """
        return usage.strip()
