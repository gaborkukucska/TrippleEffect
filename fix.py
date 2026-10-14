#!/usr/bin/env python3
"""
TrippleEffect: Round 1f Surgical Fix
- Hard cap project title length to 50 characters to prevent [Errno 36] File name too long
- Defensive filename capping & OSError guard in http_routes, file_system, sandbox_runner
- Remove file_system tool distraction from PM_STATE_STARTUP in core.py
- Clarify short project title instruction in admin_ai_planning_prompt (prompts.yaml)

Usage:
    python3 patch_round1f.py --dry-run
    python3 patch_round1f.py
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()

PATCHES = [
    # --------------------------------------------------------------------------
    # 1. src/workflows/project_creation_workflow.py
    # --------------------------------------------------------------------------
    {
        "file": "src/workflows/project_creation_workflow.py",
        "find": """        title_value = data_input.get("title")
        if title_value and isinstance(title_value, str):
            raw_title = html.unescape(title_value.strip())
            # Sanitize project title: replace spaces and non-alphanumeric characters with underscores
            sanitized_title = re.sub(r'[^\w]', '_', raw_title)
            # Compress multiple underscores into one
            project_title = re.sub(r'_+', '_', sanitized_title).strip('_')
            logger.info(f"ProjectCreationWorkflow: Extracted and sanitized title='{project_title}' from JSON (Original: '{raw_title}').")
        else:""",
        "replace": """        title_value = data_input.get("title")
        if title_value and isinstance(title_value, str):
            raw_title = html.unescape(title_value.strip())
            # Sanitize project title: replace spaces and non-alphanumeric characters with underscores
            sanitized_title = re.sub(r'[^\w]', '_', raw_title)
            # Compress multiple underscores into one
            project_title = re.sub(r'_+', '_', sanitized_title).strip('_')
            # Hard cap length to 50 characters to strictly adhere to filesystem NAME_MAX (255)
            # and allow room for subdirectories, team prefixes, and session names.
            MAX_PROJECT_TITLE_LEN = 50
            if len(project_title) > MAX_PROJECT_TITLE_LEN:
                logger.warning(f"ProjectCreationWorkflow: Project title '{project_title}' exceeded {MAX_PROJECT_TITLE_LEN} chars ({len(project_title)}). Truncating.")
                project_title = project_title[:MAX_PROJECT_TITLE_LEN].rstrip('_')
            if not project_title:
                import time
                project_title = f"Project_{int(time.time())}"
            logger.info(f"ProjectCreationWorkflow: Extracted and sanitized title='{project_title}' from JSON (Original: '{raw_title}').")
        else:"""
    },

    # --------------------------------------------------------------------------
    # 2. src/agents/core.py
    # --------------------------------------------------------------------------
    {
        "file": "src/agents/core.py",
        "find": """                    elif self.agent_type == AGENT_TYPE_PM:
                        if self.state == PM_STATE_STARTUP and tool_name not in ['file_system', 'tool_information']:
                            continue
                        if self.state == PM_STATE_BUILD_TEAM_TASKS and tool_name not in ['manage_team', 'tool_information', 'mark_message_read', 'send_message']:
                            continue""",
        "replace": """                    elif self.agent_type == AGENT_TYPE_PM:
                        if self.state == PM_STATE_STARTUP:
                            # In startup, PM must focus strictly on outputting the JSON kickoff plan
                            # No tools needed; pseudo-tool request_state is available if needed.
                            continue
                        if self.state == PM_STATE_BUILD_TEAM_TASKS and tool_name not in ['manage_team', 'tool_information', 'mark_message_read', 'send_message']:
                            continue"""
    },

    # --------------------------------------------------------------------------
    # 3. src/api/http_routes.py
    # --------------------------------------------------------------------------
    {
        "file": "src/api/http_routes.py",
        "find": """                    import re
                    safe_project_name = re.sub(r'[^\w\-. ]', '_', pm_project_name)
                    workspace_path = settings.PROJECTS_BASE_DIR / safe_project_name / pm_session_name / "shared_workspace" """,
        "replace": """                    import re
                    safe_project_name = re.sub(r'[^\w\-. ]', '_', pm_project_name)[:50].rstrip('_')
                    workspace_path = settings.PROJECTS_BASE_DIR / safe_project_name / pm_session_name / "shared_workspace" """
    },
    {
        "file": "src/api/http_routes.py",
        "find": """    safe_project_name = re_module.sub(r'[^\w\-. ]', '_', project_name)
    session_dir = settings.PROJECTS_BASE_DIR / safe_project_name / session_name""",
        "replace": """    safe_project_name = re_module.sub(r'[^\w\-. ]', '_', project_name)[:50].rstrip('_')
    session_dir = settings.PROJECTS_BASE_DIR / safe_project_name / session_name"""
    },

    # --------------------------------------------------------------------------
    # 4. src/tools/file_system.py
    # --------------------------------------------------------------------------
    {
        "file": "src/tools/file_system.py",
        "find": """            safe_project_name = project_name.replace(" ", "_").strip()
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{safe_project_name}/{session_name}'" """,
        "replace": """            safe_project_name = project_name.replace(" ", "_").strip()[:50].rstrip('_')
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{safe_project_name}/{session_name}'" """
    },

    # --------------------------------------------------------------------------
    # 5. src/tools/sandbox_runner.py
    # --------------------------------------------------------------------------
    {
        "file": "src/tools/sandbox_runner.py",
        "find": """    raw = Path(projects_base) / project_name / session_name / "shared_workspace"
    safe = Path(projects_base) / project_name.replace(" ", "_").strip() / session_name / "shared_workspace"
    if safe.is_dir():
        return safe
    if raw.is_dir():
        return raw
    return safe""",
        "replace": """    safe_name = project_name.replace(" ", "_").strip()[:50].rstrip('_')
    raw = Path(projects_base) / project_name[:50].rstrip('_') / session_name / "shared_workspace"
    safe = Path(projects_base) / safe_name / session_name / "shared_workspace"
    try:
        if safe.is_dir():
            return safe
    except OSError:
        pass
    try:
        if raw.is_dir():
            return raw
    except OSError:
        pass
    return safe"""
    },

    # --------------------------------------------------------------------------
    # 6. prompts.yaml
    # --------------------------------------------------------------------------
    {
        "file": "prompts.yaml",
        "find": """  3.  **Define Project Title:** Think of a concise title for this project.""",
        "replace": """  3.  **Define Project Title:** Think of a short, concise title for this project (2 to 5 words, max 40 characters, e.g. "Word Stats CLI"). Do NOT put a description, checklist, or sentence in the title."""
    }
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Simulate patches without writing files")
    args = ap.parse_args()

    if not (ROOT / "src").is_dir():
        print("ERROR: Run this script from the TrippleEffect repository root.", file=sys.stderr)
        return 2

    files_to_patch = {}
    for p in PATCHES:
        files_to_patch.setdefault(p["file"], []).append(p)

    errors = []
    file_results = {}

    for rel_path, file_patches in files_to_patch.items():
        path = ROOT / rel_path
        if not path.is_file():
            errors.append(f"Target file not found: {rel_path}")
            continue

        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        orig_text = text

        for i, patch in enumerate(file_patches, 1):
            find_str = patch["find"].replace("\r\n", "\n").strip()
            replace_str = patch["replace"].replace("\r\n", "\n").strip()

            # Normalize multi-space / trailing space issues on find
            if find_str not in text:
                # Try finding stripped
                found = False
                for line in find_str.splitlines():
                    if line.strip() not in text:
                        found = False
                        break
                    found = True
                errors.append(f"Match not found in {rel_path} for patch #{i}:\n{find_str[:120]}...")
            elif text.count(find_str) > 1:
                errors.append(f"Ambiguous match ({text.count(find_str)} occurrences) in {rel_path} for patch #{i}")
            else:
                text = text.replace(find_str, replace_str, 1)

        file_results[rel_path] = (orig_text, text)

    if errors:
        print("PATCH ABORTED — the following issues occurred:", file=sys.stderr)
        for err in errors:
            print(f"  ❌ {err}", file=sys.stderr)
        return 1

    for rel_path, (orig, patched) in file_results.items():
        if orig == patched:
            print(f"No changes needed for {rel_path}")
            continue

        if args.dry_run:
            print(f"--- [DRY RUN] Diff for {rel_path} ---")
            diff = difflib.unified_diff(
                orig.splitlines(True),
                patched.splitlines(True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}"
            )
            sys.stdout.writelines(diff)
            print()
        else:
            (ROOT / rel_path).write_text(patched, encoding="utf-8")
            print(f"✅ Patched {rel_path}")

    mode = "DRY RUN COMPLETE — all patches valid!" if args.dry_run else "Successfully patched all files!"
    print(f"\n{mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
