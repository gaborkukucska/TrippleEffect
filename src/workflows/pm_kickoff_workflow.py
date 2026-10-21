# START OF FILE src/workflows/pm_kickoff_workflow.py
import logging
import json
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .base import BaseWorkflow, WorkflowResult
from src.agents.constants import (
    AGENT_TYPE_PM,
    PM_STATE_STARTUP, PM_STATE_BUILD_TEAM_TASKS,
    AGENT_STATUS_IDLE
)
from src.config.settings import settings
from src.tools.sandbox_runner import resolve_shared_workspace

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)


# --- Project structure parsing (Round 1c) -------------------------------------------
# The PM's "project_structure" is free-form JSON. The harness must accept every
# reasonable shape and must never turn a file name into a directory: a
# {"dir": "__init__.py"} entry once produced src/__init__.py/ as a folder and blocked
# the worker that later tried to write the real file.

# Extension-less names that are files, not directories.
_KNOWN_FILE_NAMES = frozenset({
    "Makefile", "Dockerfile", "LICENSE", "Procfile", "Gemfile", "Rakefile", "Vagrantfile",
    "Jenkinsfile", "Pipfile", "CNAME", "README", "CHANGELOG",
    ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig", ".env", ".npmrc",
    ".prettierrc", ".eslintrc", ".babelrc", ".nvmrc", ".python-version", ".flake8",
})
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*$")


# Round 1v: the plan said "in the root directory" and the PM wrote {"dir": "root/temp_convert.py", "files": ["temp_convert.py"]}.
# The framework created root/ and an empty directory root/temp_convert.py/, and the tests could not import the module.
_ROOT_ALIASES = frozenset({"root", "project_root", "workspace_root", "shared_workspace", "<root>", "$root"})
# A "dir" whose last part ends in one of these is a file even if the PM gave it children.
_DEFINITE_FILE_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".rst", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".csv", ".xml", ".sql",
})


def _strip_root_alias(rel: str) -> str:
    """'root/x.py' -> 'x.py', 'root' -> '' (the workspace root itself); anything else unchanged."""
    parts = rel.split("/") if rel else []
    if parts and parts[0].lower() in _ROOT_ALIASES:
        return "/".join(parts[1:])
    return rel


def _is_definitely_file(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in _DEFINITE_FILE_EXTENSIONS


def looks_like_file(name: str) -> bool:
    """True if the last path component reads as a file name (has an extension or is a known file)."""
    base = PurePosixPath(name.replace("\\", "/").rstrip("/")).name
    if not base:
        return False
    if base in _KNOWN_FILE_NAMES:
        return True
    if base.startswith(".") and base.count(".") == 1:
        return False  # dot-directory such as .github / .vscode
    return bool(_EXTENSION_RE.search(base))


def _clean_rel(name: Any) -> Optional[str]:
    """Normalises one path fragment; None if not a string, absolute, or escaping the workspace."""
    if not isinstance(name, str):
        return None
    raw = name.strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)  # "" means the parent itself (e.g. "dir": ".")


def plan_project_structure(structure: Any) -> Tuple[List[str], List[str], List[str]]:
    """
    Parses a kickoff "project_structure" into (directories, planned_files, notes).

    Accepted shapes (any mix, nested through "children"):
      {"dir": "src", "children": [...], "files": ["main.py", {"file": "x.py"}]}
      {"file": "README.md"}  /  {"files": [...]}  /  "src/"  /  "main.py"
    A "dir" entry whose name looks like a file (and has no children) is recorded as
    a planned file. Paths are POSIX-style and relative to the workspace root.
    """
    dirs: List[str] = []
    files: List[str] = []
    notes: List[str] = []

    def add_dir(rel: str) -> None:
        if rel and rel not in dirs:
            dirs.append(rel)

    def add_file(rel: str) -> None:
        if rel and rel not in files:
            files.append(rel)
            parent = str(PurePosixPath(rel).parent)
            if parent not in (".", ""):
                add_dir(parent)

    def join(parent: str, child: str) -> str:
        return f"{parent}/{child}" if parent and child else (parent or child)

    def walk(items: Any, parent: str) -> None:
        if isinstance(items, (str, dict)):
            items = [items]
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, str):
                rel = _clean_rel(item)
                if rel and not parent:
                    rel = _strip_root_alias(rel)
                if not rel:
                    continue
                full = join(parent, rel)
                if looks_like_file(rel) and not item.rstrip().endswith("/"):
                    add_file(full)
                else:
                    add_dir(full)
                continue
            if not isinstance(item, dict):
                continue
            if "dir" in item or "directory" in item or "folder" in item:
                raw = item.get("dir", item.get("directory", item.get("folder")))
                rel = _clean_rel(raw)
                if rel is None:
                    if raw not in (None, ""):
                        notes.append(f"Skipped unsafe or invalid directory entry: {raw!r}")
                    continue
                if rel and not parent:
                    stripped = _strip_root_alias(rel)
                    if stripped != rel:
                        notes.append(f"'{rel}': '{rel.split('/')[0]}' means the workspace root; recorded as '{stripped or '.'}'.")
                        rel = stripped
                full = join(parent, rel)
                has_children = bool(item.get("children")) or bool(item.get("files"))
                if rel and looks_like_file(rel) and (not has_children or _is_definitely_file(rel)):
                    notes.append(f"'{full}' was listed as a directory but looks like a file; recorded as a planned file.")
                    add_file(full)
                    if has_children:  # its "children" belong next to it, in its parent directory
                        file_parent = str(PurePosixPath(full).parent)
                        file_parent = "" if file_parent == "." else file_parent
                        walk(item.get("children", []), file_parent)
                        walk(item.get("files", []), file_parent)
                    continue
                add_dir(full)
                walk(item.get("children", []), full)
                walk(item.get("files", []), full)
                continue
            if "file" in item or "path" in item or "name" in item:
                raw = item.get("file", item.get("path", item.get("name")))
                rel = _clean_rel(raw)
                if rel and not parent:
                    rel = _strip_root_alias(rel)
                if rel:
                    add_file(join(parent, rel))
                elif raw not in (None, ""):
                    notes.append(f"Skipped unsafe or invalid file entry: {raw!r}")
                continue
            if "files" in item or "children" in item:
                walk(item.get("files", []), parent)
                walk(item.get("children", []), parent)

    walk(structure, "")
    return dirs, files, notes


# --- Planned layout normalisation + pytest scaffold (Round 1u) --------------------------------------------------------------
# 2026-10-16 18:05: the PM planned temp_convert.py and test_temp_convert.py at the workspace ROOT but also declared empty src/ and
# tests/ directories. Workers then moved files around, rewrote conftest.py again and again, and the finished project failed all
# 15 tests with ModuleNotFoundError. The framework now makes the layout consistent and owns the import path.
FRAMEWORK_CONFTEST_MARKER = "FRAMEWORK-OWNED conftest"
FRAMEWORK_CONFTEST = "\n".join([
    '"""FRAMEWORK-OWNED conftest: makes modules in src/ and in the project root importable from the tests.',
    '',
    'Do not edit or recreate this file. In tests simply write `from my_module import my_function`.',
    '"""',
    'import sys',
    'from pathlib import Path',
    '',
    '_ROOT = Path(__file__).resolve().parent',
    'for _path in (_ROOT / "src", _ROOT):',
    '    if _path.is_dir() and str(_path) not in sys.path:',
    '        sys.path.insert(0, str(_path))',
    '',
])
_TEST_FILE_RE = re.compile(r"^(test_.+|.+_test)\.py$", re.IGNORECASE)
_ROOT_PY_KEEP = frozenset({"conftest.py", "setup.py", "manage.py", "noxfile.py", "tasks.py", "wsgi.py", "asgi.py"})


def normalize_planned_layout(dirs: List[str], files: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """
    Makes a planned layout consistent: Python test files always live in tests/; other Python files planned at the workspace
    root move to src/ when the plan declares a src/ directory. Returns (dirs, files, notes).
    """
    new_dirs = list(dirs)
    new_files: List[str] = []
    notes: List[str] = []
    has_src = "src" in new_dirs or any(d.startswith("src/") for d in new_dirs)

    def add_dir(rel: str) -> None:
        if rel not in new_dirs:
            new_dirs.append(rel)

    for rel in files:
        pure = PurePosixPath(rel)
        target = rel
        if _TEST_FILE_RE.match(pure.name) and pure.parts[0] != "tests":
            target = f"tests/{pure.name}"
        elif len(pure.parts) == 1 and pure.suffix == ".py" and has_src and pure.name not in _ROOT_PY_KEEP:
            target = f"src/{pure.name}"
        if target != rel:
            notes.append(f"Planned file '{rel}' moved to '{target}' to keep tests/ and src/ consistent.")
            add_dir(str(PurePosixPath(target).parent))
        if target not in new_files:
            new_files.append(target)
    return new_dirs, new_files, notes


_MANAGER_ROLE_RE = re.compile(r"(^|[^a-z0-9])(pm\d*|project[\s_\-]*manager|projectmanager)($|[^a-z0-9])", re.IGNORECASE)


# Round 2i: PM1 (2026-10-20 07:26) wrote roles as objects ({"name": "Coder", "description": "..."}) and tasks with "title"
# instead of "description".
_ROLE_NAME_KEYS = ("name", "role", "title", "id")
TASK_TEXT_KEYS = ("description", "title", "task", "name", "summary")


def role_name_of(role: Any) -> Any:
    """A role given as an object becomes its name; anything else is returned unchanged."""
    if isinstance(role, dict):
        for key in _ROLE_NAME_KEYS:
            value = role.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return role


def task_text_of(task_info: Any) -> str:
    """The text of a kick-off task: 'description', else 'title' / 'task' / 'name' / 'summary' ('' if none)."""
    if not isinstance(task_info, dict):
        return ""
    for key in TASK_TEXT_KEYS:
        value = task_info.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def sanitize_worker_roles(roles: List[Any]) -> Tuple[List[Any], List[str]]:
    """
    Returns (kept_roles, dropped_manager_roles). The PM is already the project manager, so manager-type
    roles (e.g. 'Project_Manager', 'PM1_Project_Manager') must not become worker agents. Duplicate roles
    (case-insensitive) are removed; non-string entries are passed through unchanged.
    """
    kept: List[Any] = []
    dropped: List[str] = []
    seen = set()
    for role in roles:
        if not isinstance(role, str):
            kept.append(role)
            continue
        name = role.strip()
        if not name:
            continue
        if _MANAGER_ROLE_RE.search(name):
            dropped.append(name)
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(name)
    return kept, dropped


def _render_definitions(value: Any, indent: int = 0) -> List[str]:
    """Renders code_base_definitions (string, list or dict from the model) as Markdown lines."""
    pad = "  " * indent
    if value is None:
        return []
    if isinstance(value, str):
        return [(pad + line) if line else "" for line in value.splitlines()]
    if isinstance(value, dict):
        out: List[str] = []
        for k, v in value.items():
            if isinstance(v, (dict, list, tuple)):
                out.append(f"{pad}- **{k}**:")
                out.extend(_render_definitions(v, indent + 1))
            else:
                out.append(f"{pad}- **{k}**: {v}")
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            if isinstance(v, dict) and v and all(not isinstance(x, (dict, list, tuple)) for x in v.values()):
                out.append(pad + "- " + "; ".join(f"{k}: {x}" for k, x in v.items()))
            elif isinstance(v, (dict, list, tuple)):
                out.append(pad + "-")
                out.extend(_render_definitions(v, indent + 1))
            else:
                out.append(f"{pad}- {v}")
        return out
    return [pad + str(value)]


def render_project_structure_md(dirs: List[str], planned_files: List[str], code_base_definitions: Any = None,
                                existing_files: Optional[List[str]] = None) -> str:
    """
    Builds PROJECT_STRUCTURE.md: standards, then a tree of directories and planned files. Round 2o: planned files that
    already exist (a continued project's files carried over from its earlier session) are listed as existing, not as
    files to create.
    """
    existing = [f for f in planned_files if f in set(existing_files or [])]
    to_create = [f for f in planned_files if f not in existing]
    lines = [
        "# Project Directory Structure",
        "",
        "This file was auto-generated by the framework based on the PM's kickoff plan.",
        "**All workers MUST place their files within the directories listed below.**",
        "Do NOT create arbitrary new top-level directories without PM approval.",
        "Directories already exist. Files listed under 'Files To Create' do NOT exist yet: create each one with file_system 'write'",
        "at exactly that path. Paths never contain notes in brackets.",
        "Run tests from the workspace root with `python -m pytest -q`. A framework-owned conftest.py already makes src/ importable: write",
        "`from my_module import my_function` in tests and NEVER create or edit the root conftest.py.",
        "",
    ]
    rendered = _render_definitions(code_base_definitions)
    if rendered:
        lines += ["## Code Base Definitions & Standards", ""] + rendered + [""]

    tree: Dict[str, Any] = {}
    for d in dirs:
        node = tree
        for part in d.split("/"):
            node = node.setdefault(part + "/", {})
    for f in planned_files:
        node = tree
        parts = f.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node.setdefault(parts[-1], None)

    def emit(node: Dict[str, Any], depth: int) -> None:
        for name in sorted(node, key=lambda n: (not n.endswith("/"), n.lower())):
            lines.append("  " * (depth + 1) + name)
            if isinstance(node[name], dict):
                emit(node[name], depth + 1)

    lines += ["## Directory Layout", "", "```", "./  (workspace root)"]
    emit(tree, 0)
    lines += ["```", ""]
    if to_create:
        # Round 1v: a separate list instead of 'name  (planned)' in the tree (W3 copied the bracket note into real paths)
        lines += ["## Files To Create", "", "These files are planned but do NOT exist yet:", ""]
        lines += [f"- {f}" for f in to_create] + [""]
    if existing:
        lines += ["## Existing Files", "", "These planned files ALREADY EXIST (carried over from the project's earlier session): "
                  "read them first and change them; do not create them again:", ""]
        lines += [f"- {f}" for f in existing] + [""]
    return "\n".join(lines)


class PMKickoffWorkflow(BaseWorkflow):
    """
    Triggered by a PM agent in PM_STATE_STARTUP outputting a kickoff plan (JSON).
    This workflow processes the plan, creates tasks in TaskWarrior, stores the
    required roles, and transitions the PM to the team building state.
    """
    name: str = "pm_project_kickoff"
    trigger_tag_name: str = "kickoff_plan"  # PM directly outputs this new tag
    allowed_agent_type: Optional[str] = AGENT_TYPE_PM
    allowed_agent_state: Optional[str] = PM_STATE_STARTUP
    description: str = (
        "Processes a PM's kickoff plan from JSON input, creating tasks "
        "in TaskWarrior, storing the identified unique roles, auto-creating the "
        "project directory structure, and transitioning the PM to the team "
        "building state."
    )
    expected_json_schema: str = (
        "{\n"
        '  "roles": ["First Unique Role (e.g., Coder)", "Second Unique Role (e.g., Technical_Writer)"],\n'
        '  "tasks": [\n'
        '    {"id": "task_1", "description": "High-level kick-off task 1 description"},\n'
        '    {"id": "task_2", "depends_on": "task_1", "description": "High-level kick-off task 2 description"}\n'
        '  ],\n'
        '  "code_base_definitions": "Define the tech stack, code conventions, testing framework, and specific code base constraints here.",\n'
        '  "project_structure": [\n'
        '    {"dir": "src", "files": ["__init__.py", "main.py"]},\n'
        '    {"dir": "tests", "files": ["test_main.py"]},\n'
        '    {"dir": "docs"},\n'
        '    {"file": "README.md"}\n'
        '  ]\n'
        "}"
    )

    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent',  # This will be the PM agent
        data_input: Any  # This is the parsed JSON dictionary
    ) -> WorkflowResult:
        logger.info(f"Executing PMKickoffWorkflow for PM agent '{agent.agent_id}'.")

        if not isinstance(data_input, dict):
            return WorkflowResult(
                success=False,
                message="Invalid kickoff_plan data format. Expected a JSON object.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )

        # --- Extract Roles ---
        role_names: List[str] = data_input.get("roles", [])
        dropped_roles: List[str] = []
        if isinstance(role_names, list):
            role_names = [role_name_of(role) for role in role_names]  # Round 2i
            role_names, dropped_roles = sanitize_worker_roles(role_names)
            if dropped_roles:
                logger.warning(f"PMKickoffWorkflow: Dropped manager-type role(s) {dropped_roles} from kickoff_plan of agent '{agent.agent_id}' (the PM is the project manager).")

        if not role_names or not isinstance(role_names, list):
            logger.warning(f"PMKickoffWorkflow: No roles found in kickoff_plan from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message=("No roles were provided in the kickoff_plan. Please provide a list of required roles." + (f" Manager roles ({', '.join(dropped_roles)}) are not allowed because you are already the project manager; list only worker roles such as 'Coder'." if dropped_roles else "")),
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(role_names)} unique roles for agent '{agent.agent_id}': {role_names}")

        # --- Extract Tasks ---
        tasks_list = data_input.get("tasks", [])
        task_descriptions: List[str] = []
        task_info_list: List[dict] = []
        
        if isinstance(tasks_list, list):
            for task_info in tasks_list:
                if isinstance(task_info, dict) and task_text_of(task_info):  # Round 2i: 'title' etc. count too
                    raw_desc = " ".join(task_text_of(task_info).splitlines()).strip()
                    dep_val = task_info.get("depends_on") or task_info.get("depends") or task_info.get("dependencies")
                    task_id_val = task_info.get("id") or task_info.get("task_id")
                    task_descriptions.append(raw_desc)
                    task_info_list.append({
                        "id": task_id_val,
                        "depends_on": dep_val,
                        "description": raw_desc
                    })
        
        if not task_descriptions:
            logger.warning(f"PMKickoffWorkflow: No tasks found in kickoff_plan from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message="No tasks were provided in the kickoff_plan. Please provide a list of kick-off tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(task_descriptions)} tasks for agent '{agent.agent_id}': {task_descriptions}")

        created_tasks_info = []
        failed_tasks_info = []
        all_tasks_created_successfully = True

        project_context = agent.agent_config.get("config", {}).get("project_name_context")
        if not project_context:
            project_context = manager.current_project
            logger.warning(f"PMKickoffWorkflow: project_name_context not found in PM agent config. Using manager.current_project: {project_context}")
        
        if not project_context:
            logger.error(f"PMKickoffWorkflow: Critical error - project context is missing for PM '{agent.agent_id}'. Cannot create tasks.")
            return WorkflowResult(
                success=False,
                message="Critical error: Project context is missing for the PM agent. Cannot create tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE
            )

        # --- Mark Initial Project Task as Decomposed ---
        try:
            from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
            if TASKLIB_AVAILABLE and project_context and manager.current_session:
                pm_tool = ProjectManagementTool()
                tw = pm_tool._get_taskwarrior_instance(project_context, manager.current_session)
                if tw:
                    # Find any pending tasks tagged with 'project_kickoff'
                    initial_tasks = tw.tasks.pending().filter('+project_kickoff', project=project_context)
                    for task in initial_tasks:
                        task['task_progress'] = 'decomposed'
                        task.save()
                        logger.info(f"PMKickoffWorkflow: Marked initial project task '{task['uuid']}' as decomposed.")
        except Exception as e:
            logger.warning(f"PMKickoffWorkflow: Failed to mark initial project task as decomposed: {e}")

        # --- Create Kickoff Sub-tasks ---
        created_task_ids: set = set()      # plan ids (aliases) of tasks created in THIS attempt
        created_task_uuids: List[str] = [] # tasks this attempt really created (not skipped duplicates), for rollback
        for i, task_info in enumerate(task_info_list):
            task_desc = task_info["description"]
            task_tool_args = {
                "action": "add_task",
                "description": f"Kick-off Task {i+1}: {task_desc}",
                "priority": "H",
                "project_filter": project_context,
                "tags": ["kickoff", "pm_decomposed", f"task_order_{i+1}"]
            }
            if task_info.get("id"):
                # Always a string: the task tool only registers an alias for string ids, and a numeric "1" would
                # otherwise be read as a Taskwarrior row number by later dependencies.
                task_tool_args["task_id"] = str(task_info["id"]).strip()
            if task_info.get("depends_on"):
                # Round 1n: keep only dependencies on tasks created earlier in THIS plan. An invented id such as
                # "temp_structure_init" (observed 2026-10-15 15:17:54) used to make the task tool fail, which failed the
                # whole kickoff after two tasks already existed; the retry then created a second, different set.
                kept_deps, dropped_deps = self._sanitize_kickoff_dependencies(task_info["depends_on"], created_task_ids)
                if dropped_deps:
                    logger.warning(f"PMKickoffWorkflow: Dropped unknown dependency id(s) {dropped_deps} for task '{task_desc[:60]}' (not tasks created earlier in this plan).")
                if kept_deps:
                    task_tool_args["depends"] = ",".join(kept_deps)
            try:
                logger.debug(f"PMKickoffWorkflow: Attempting to create task via ToolExecutor: {task_tool_args}")
                task_result = await manager.tool_executor.execute_tool(
                    agent_id=agent.agent_id, 
                    agent_sandbox_path=agent.sandbox_path,
                    tool_name="project_management",
                    tool_args=task_tool_args,
                    project_name=project_context, 
                    session_name=manager.current_session,
                    manager=manager 
                )

                if isinstance(task_result, dict) and task_result.get("status") == "success":
                    task_id = task_result.get("task_id", "N/A")
                    task_uuid = task_result.get("task_uuid", "N/A")
                    created_tasks_info.append(f"Task '{task_desc[:30]}...' (ID: {task_id}, UUID: {task_uuid})")
                    if task_info.get("id"):
                        created_task_ids.add(str(task_info["id"]).strip())
                    # The task tool answers "success" for a skipped near-duplicate and returns the EXISTING task's uuid:
                    # that task is not ours and must never be rolled back.
                    if "skipped to prevent duplication" not in str(task_result.get("message", "")).lower() and task_uuid != "N/A":
                        created_task_uuids.append(str(task_uuid))
                    logger.info(f"PMKickoffWorkflow: Successfully created task (ID: {task_id}): {task_desc}")
                else:
                    all_tasks_created_successfully = False
                    error_detail = task_result.get("message", "Unknown error") if isinstance(task_result, dict) else str(task_result)
                    failed_tasks_info.append(f"Task '{task_desc[:30]}...': {error_detail}")
                    logger.error(f"PMKickoffWorkflow: Failed to create task '{task_desc}': {error_detail}. Full result: {task_result}")

            except Exception as e:
                all_tasks_created_successfully = False
                failed_tasks_info.append(f"Task '{task_desc[:30]}...': Exception - {str(e)}")
                logger.error(f"PMKickoffWorkflow: Exception creating task '{task_desc}': {e}", exc_info=True)

        # --- Create Project Directory Structure ---
        structure_list = data_input.get("project_structure", [])
        code_base_definitions = data_input.get("code_base_definitions")
        # Round 2a: files the kick-off tasks name with a directory (e.g. 'tests/test_x.py') are planned files too. On
        # 2026-10-18 the structure listed only 'src/': no tests/ directory and no conftest.py were created.
        from src.utils.task_files import file_paths_in_text
        task_named_files: List[str] = []
        for task_text in task_descriptions:
            for named_path in file_paths_in_text(task_text, with_directory_only=True):
                if named_path not in task_named_files:
                    task_named_files.append(named_path)
        
        created_dirs: List[str] = []
        if (structure_list or task_named_files) and project_context and manager.current_session:
            workspace_path = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_context), str(manager.current_session))
            try:
                structure_items = list(structure_list) if isinstance(structure_list, list) else ([structure_list] if structure_list else [])
                planned_dirs, planned_files, notes = plan_project_structure(structure_items + task_named_files)
                planned_dirs, planned_files, layout_notes = normalize_planned_layout(planned_dirs, planned_files)
                notes = list(notes) + layout_notes
                for note in notes:
                    logger.warning(f"PMKickoffWorkflow: project_structure from PM '{agent.agent_id}': {note}")
                created_dirs = self._create_directory_structure(planned_dirs, workspace_path)
                self._write_test_scaffold(workspace_path, planned_files)
                if created_dirs or planned_files:
                    # Generate PROJECT_STRUCTURE.md manifest (directories + planned files)
                    self._write_project_structure_md(workspace_path, created_dirs, code_base_definitions, planned_files)
                    logger.info(f"PMKickoffWorkflow: Auto-created {len(created_dirs)} directories, recorded {len(planned_files)} planned files and wrote PROJECT_STRUCTURE.md for project '{project_context}'.")
                else:
                    logger.info("PMKickoffWorkflow: project_structure was present but contained no directories or files.")
            except Exception as e:
                logger.error(f"PMKickoffWorkflow: Failed to create project directory structure: {e}", exc_info=True)
                # Non-fatal — continue with the rest of the kickoff
        else:
            logger.warning(f"PMKickoffWorkflow: No project_structure section found in kickoff plan from PM '{agent.agent_id}'. Skipping directory auto-creation.")

        if all_tasks_created_successfully:
            logger.info(f"PMKickoffWorkflow: All tasks created for PM '{agent.agent_id}'. Preparing successful result with reschedule.")
            agent.clear_history()
            agent._last_system_prompt_state = None  # Force fresh prompt generation after history clear
            logger.info(f"PMKickoffWorkflow: Cleared history for PM agent '{agent.agent_id}' before transitioning to PM_STATE_BUILD_TEAM_TASKS.")

            # Store the identified roles and tasks on the agent for the next state
            agent.kick_off_roles = role_names
            agent.kick_off_tasks = task_descriptions
            agent.target_worker_agents_for_build = len(role_names)  # *** FIX: Set the target number of agents to build ***
            agent.successfully_created_agent_count_for_build = 0 # Reset counter
            logger.info(f"PMKickoffWorkflow: Stored {len(role_names)} roles, {len(task_descriptions)} tasks, and set target agent count to {len(role_names)} on agent '{agent.agent_id}'.")

            # --- BEGIN MODIFICATION: Mark initial Admin AI task as done ---
            logger.info(f"PMKickoffWorkflow: Attempting to find and complete the initial project plan task for project '{project_context}' assigned to PM '{agent.agent_id}'.")
            initial_project_task_uuid_to_complete = None
            try:
                list_tasks_args = {
                    "action": "list_tasks",
                    "project_filter": project_context,
                    "tags_filter": "project_kickoff,auto_created_by_framework"
                }
                logger.debug(f"PMKickoffWorkflow: Listing tasks with args: {list_tasks_args}")
                list_result = await manager.tool_executor.execute_tool(
                    agent_id="framework", # System action
                    agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                    tool_name="project_management",
                    tool_args=list_tasks_args,
                    project_name=project_context,
                    session_name=manager.current_session,
                    manager=manager
                )

                if isinstance(list_result, dict) and list_result.get("status") == "success":
                    tasks = list_result.get("tasks", [])
                    logger.debug(f"PMKickoffWorkflow: Found {len(tasks)} candidate tasks for initial project plan task.")
                    found_matching_tasks = []
                    for task_item in tasks:
                        if task_item.get("status") != "completed":
                            found_matching_tasks.append(task_item)

                    if len(found_matching_tasks) > 1:
                        logger.warning(f"PMKickoffWorkflow: Multiple ({len(found_matching_tasks)}) non-completed initial tasks found for PM '{agent.agent_id}' in project '{project_context}'. Will attempt to complete the first one found: {found_matching_tasks[0].get('uuid')}")

                    if found_matching_tasks:
                        initial_project_task_uuid_to_complete = found_matching_tasks[0].get("uuid")
                        logger.info(f"PMKickoffWorkflow: Identified initial project plan task to complete. UUID: {initial_project_task_uuid_to_complete}, Description: '{found_matching_tasks[0].get('description', '')[:50]}...'")
                    else:
                        logger.warning(f"PMKickoffWorkflow: No suitable non-completed initial task found for PM '{agent.agent_id}' in project '{project_context}'. It might have been completed manually or does not exist.")
                else:
                    error_msg = list_result.get("message", "Unknown error") if isinstance(list_result, dict) else str(list_result)
                    logger.error(f"PMKickoffWorkflow: Failed to list tasks to find initial project plan task: {error_msg}")

            except Exception as e_list:
                logger.error(f"PMKickoffWorkflow: Exception while trying to list tasks for initial project plan task: {e_list}", exc_info=True)

            if initial_project_task_uuid_to_complete:
                try:
                    complete_task_args = {
                        "action": "complete_task",
                        "task_id": initial_project_task_uuid_to_complete
                    }
                    logger.debug(f"PMKickoffWorkflow: Attempting to complete task with args: {complete_task_args}")
                    complete_result = await manager.tool_executor.execute_tool(
                        agent_id="framework", # System action
                        agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                        tool_name="project_management",
                        tool_args=complete_task_args,
                        project_name=project_context,
                        session_name=manager.current_session,
                        manager=manager
                    )
                    if isinstance(complete_result, dict) and complete_result.get("status") == "success":
                        logger.info(f"PMKickoffWorkflow: Successfully marked initial project plan task '{initial_project_task_uuid_to_complete}' as completed.")
                    else:
                        error_msg = complete_result.get("message", "Unknown error") if isinstance(complete_result, dict) else str(complete_result)
                        logger.error(f"PMKickoffWorkflow: Failed to complete initial project plan task '{initial_project_task_uuid_to_complete}': {error_msg}")
                except Exception as e_complete:
                    logger.error(f"PMKickoffWorkflow: Exception while trying to complete initial project plan task '{initial_project_task_uuid_to_complete}': {e_complete}", exc_info=True)
            # --- END MODIFICATION ---

            # NOTE: create_team and tool_information auto-execution has been REMOVED
            # from this workflow. The cycle_handler.py handles these operations when
            # the PM naturally calls those tools, preventing double-execution that
            # caused confusion and duplicate actions.
            team_id = f"team_{project_context}"

            # Build the kickoff plan summary as a JSON block
            first_role_to_create = role_names[0] if role_names else "Worker"
            plan_summary = json.dumps({"roles": role_names, "tasks": task_info_list}, indent=2)

            final_directive_content = (
                "[Framework System Message]\n"
                "Your previous state 'pm_startup' and its associated plan are complete.\n\n"
                "**MASTER KICKOFF PLAN SUMMARY**\n"
                f"```json\n{plan_summary}\n```\n\n"
                f"You are NOW in state 'pm_build_team_tasks'.\n\n"
                f"**YOUR WORKFLOW:**\n"
                f'Step 1: Create a team using the manage_team tool with action "create_team" and team_id "{team_id}"\n'
                f"Step 2: The framework will automatically retrieve create_agent tool info for you.\n"
                f"Step 3: Create one worker agent per role listed above. Do NOT create duplicate roles.\n"
                f"Step 4: Once all roles are filled, request state change to 'pm_activate_workers'.\n\n"
                f"Your MANDATORY FIRST ACTION is Step 1: Create the team '{team_id}'.\n"
                f"Your FIRST role to create after the team is formed will be: **'{first_role_to_create}'**."
            )

            agent.message_history.append({"role": "user", "content": final_directive_content})
            logger.info(f"PMKickoffWorkflow: Injected directive user message for PM agent '{agent.agent_id}' for state PM_STATE_BUILD_TEAM_TASKS, detailing {len(task_descriptions)} kick-off tasks with explicit numbering.")

            return WorkflowResult(
                success=True,
                message=f"All {len(task_descriptions)} kick-off tasks created successfully by PM '{agent.agent_id}'. Details: {'; '.join(created_tasks_info)}",
                workflow_name=self.name,
                next_agent_state=PM_STATE_BUILD_TEAM_TASKS,
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={
                    "type": "info", 
                    "agent_id": agent.agent_id, 
                    "content": f"PM '{agent.agent_id}' successfully decomposed plan and created {len(task_descriptions)} kick-off tasks. Stored task count for build phase. Moving to build team."
                },
                tasks_to_schedule=[(agent, 0)]
            )
        else:
            # Round 1n: the PM will resubmit the whole plan after a failure. Remove what this attempt created, otherwise
            # the retry (usually with different task wording) leaves duplicates behind.
            removed_count = self._rollback_created_tasks(project_context, manager.current_session, created_task_uuids)
            error_summary = f"PM '{agent.agent_id}' failed to create some kick-off tasks. Successfully created: {len(created_tasks_info)}. Failed: {len(failed_tasks_info)} - Details: {'; '.join(failed_tasks_info)}"
            if removed_count:
                error_summary += f" The {removed_count} task(s) created during this attempt were removed again; submit the COMPLETE kickoff plan again."
            logger.warning(f"PMKickoffWorkflow: Some tasks failed for PM '{agent.agent_id}'. Preparing failure result with reschedule. Summary: {error_summary}")
            return WorkflowResult(
                success=False,
                message=error_summary,
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={"type": "error", "agent_id": agent.agent_id, "content": error_summary},
                tasks_to_schedule=[(agent, 0)] 
            )

    @staticmethod
    def _sanitize_kickoff_dependencies(raw: Any, created_ids: set) -> Tuple[List[str], List[str]]:
        """
        Splits a kickoff task's depends_on (string, comma list, JSON-ish list) into (kept, dropped). Only ids of tasks
        created earlier in the same plan are kept; anything else (invented ids, Taskwarrior row numbers, stale
        aliases) is dropped so the task is still created, just without that dependency.
        """
        items = [str(x) for x in raw] if isinstance(raw, (list, tuple, set)) else [str(raw)]
        tokens: List[str] = []
        for item in items:
            cleaned = item.replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            tokens.extend(t.strip() for t in cleaned.split(",") if t.strip())
        kept: List[str] = []
        dropped: List[str] = []
        for token in tokens:
            if token.lower() in ("none", "null"):
                continue
            (kept if token in created_ids else dropped).append(token)
        return kept, dropped

    def _rollback_created_tasks(self, project_context: Any, session: Any, uuids: List[str]) -> int:
        """Deletes tasks created by a failed kickoff attempt and their aliases. Returns how many were removed."""
        if not uuids or not project_context or not session:
            return 0
        removed = 0
        try:
            from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
            if not TASKLIB_AVAILABLE:
                return 0
            pm_tool = ProjectManagementTool()
            tw = pm_tool._get_taskwarrior_instance(project_context, session)
            if not tw:
                return 0
            for task_uuid in uuids:
                try:
                    tw.tasks.get(uuid=task_uuid).delete()
                    removed += 1
                except Exception as e:
                    logger.warning(f"PMKickoffWorkflow: Rollback could not delete task '{task_uuid}': {e}")
            try:
                aliases = pm_tool._load_aliases(project_context, session)
                stale = [name for name, target in aliases.items() if target in set(uuids)]
                for name in stale:
                    aliases.pop(name, None)
                if stale:
                    pm_tool._save_aliases(project_context, session, aliases)
            except Exception as e:
                logger.warning(f"PMKickoffWorkflow: Rollback could not clean task aliases: {e}")
            logger.warning(f"PMKickoffWorkflow: Rolled back {removed} of {len(uuids)} task(s) created by the failed kickoff attempt.")
        except Exception as e:
            logger.warning(f"PMKickoffWorkflow: Rollback of failed kickoff attempt failed: {e}")
        return removed

    def _create_directory_structure(self, planned_dirs: List[str], workspace_path: Path) -> List[str]:
        """
        Creates the planned directories (already parsed and sanitised by
        plan_project_structure) under the shared workspace. Never creates a
        directory over an existing file. Returns the relative paths created or
        already present.
        """
        created: List[str] = []
        root = workspace_path.resolve()
        for rel in planned_dirs:
            target = (workspace_path / rel).resolve()
            if not (target == root or target.is_relative_to(root)):
                logger.warning(f"PMKickoffWorkflow: Skipping directory outside workspace: {rel}")
                continue
            if target.exists() and not target.is_dir():
                logger.warning(f"PMKickoffWorkflow: Not creating directory '{rel}': a file with that name exists.")
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:  # e.g. a parent path component is a file
                logger.warning(f"PMKickoffWorkflow: Could not create directory '{rel}': {e}")
                continue
            created.append(rel)
            logger.debug(f"PMKickoffWorkflow: Created directory: {rel}")
        return created

    def _write_test_scaffold(self, workspace_path: Path, planned_files: List[str]) -> None:
        """Writes the framework-owned root conftest.py when Python files are planned (never over an existing one)."""
        try:
            if not any(f.endswith(".py") for f in planned_files):
                return
            conftest = workspace_path / "conftest.py"
            if conftest.exists():
                return
            workspace_path.mkdir(parents=True, exist_ok=True)
            conftest.write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
            logger.info(f"PMKickoffWorkflow: Wrote framework-owned conftest.py to {conftest}")
        except OSError as e:
            logger.warning(f"PMKickoffWorkflow: Could not write conftest.py: {e}")

    def _write_project_structure_md(self, workspace_path: Path, created_dirs: List[str],
                                    code_base_definitions: Any = None,
                                    planned_files: Optional[List[str]] = None) -> None:
        """
        Write a PROJECT_STRUCTURE.md file into the workspace root that documents
        the pre-defined directory layout and planned files. Workers are instructed
        to read this file before creating any new files or directories.
        """
        existing_files = [f for f in (planned_files or []) if (workspace_path / f).is_file()]  # Round 2o: a continued project
        content = render_project_structure_md(created_dirs, planned_files or [], code_base_definitions, existing_files)
        workspace_path.mkdir(parents=True, exist_ok=True)
        md_path = workspace_path / "PROJECT_STRUCTURE.md"
        md_path.write_text(content, encoding="utf-8")
        logger.info(f"PMKickoffWorkflow: Wrote PROJECT_STRUCTURE.md to {md_path}")
# END OF FILE src/workflows/pm_kickoff_workflow.py
