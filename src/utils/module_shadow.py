# START OF FILE src/utils/module_shadow.py
"""
Round 5p: no Python folder beside a module of the same name (and no module beside a package of the same name).

2026-11-20 16:20 PM1 created the task "Create src/server/__init__.py to make server a proper Python package" while
src/server.py existed; src/server/player.py, src/server/websocket.py and later src/leaderboard/ (beside src/leaderboard.py)
followed. A folder with __init__.py wins over the module of the same name, so `from src.server import ...` no longer found
what src/server.py defined (and a folder without one makes `src.server.player` "not a package"). W1's test task failed
with ImportError from 17:29 until the run ended at 22:36 - 4 hours of import fixes that could not work.

A new .py file is not written when it would create the folder `x/` beside an existing `x.py` (or put code into such a
folder that has no __init__.py yet), or when it is a new `x.py` beside a package folder `x/` (with __init__.py). The
refusal says what to do instead: a file beside the module, or MOVE the module into the folder first.
"""
import logging
from pathlib import Path, PurePosixPath
from typing import Any, Optional

logger = logging.getLogger(__name__)


def module_shadow_problem(workspace: Any, rel: str) -> Optional[str]:
    """The refusal for a NEW Python file 'rel' (relative to the workspace) that would hide or be hidden by a module, or None."""
    try:
        base = Path(workspace).resolve()
        rel = str(rel or "").replace("\\", "/").lstrip("./")
        path = PurePosixPath(rel)
        if path.suffix != ".py" or (base / rel).exists():
            return None
        parts = path.parts
        # 1) a folder beside a module of the same name
        for i in range(1, len(parts)):
            folder = base.joinpath(*parts[:i])
            module = base.joinpath(*parts[:i - 1], parts[i - 1] + ".py")
            if not module.is_file():
                continue
            if folder.is_dir() and (folder / "__init__.py").is_file():
                return None  # the clash is already there: do not block its repair
            folder_rel = "/".join(parts[:i])
            module_rel = "/".join(parts[:i - 1] + (parts[i - 1] + ".py",))
            dotted = ".".join(parts[:i])
            beside = "/".join(parts[:i - 1] + (f"{parts[i - 1]}_{path.stem if path.stem != '__init__' else 'package'}.py",))
            return (f"NOT written: {rel} would make the folder {folder_rel}/ beside {module_rel}. Python then imports "
                    f"`{dotted}` from the folder and no longer finds what {module_rel} defines: every `from {dotted} import "
                    f"...` breaks (2026-11-20 src/server/ beside src/server.py cost a run 4 hours of ImportError). Do one of:\n"
                    f"- put the new code in a file beside it, e.g. {beside} (import it as `{beside[:-3].replace('/', '.')}`), "
                    f"or into {module_rel} itself;\n"
                    f"- or first MOVE {module_rel} into the folder (file_system action='move' {module_rel} -> "
                    f"{folder_rel}/{parts[i - 1]}.py), write {folder_rel}/__init__.py with `from .{parts[i - 1]} import ...` "
                    f"for the names the project imports from `{dotted}`, and then write {rel}.")
        # 2) a module beside a package of the same name
        if path.name != "__init__.py":
            package = base / str(path.with_suffix(""))
            if (package / "__init__.py").is_file():
                package_rel = str(path.with_suffix(""))
                dotted = package_rel.replace("/", ".")
                return (f"NOT written: {rel} would sit beside the package {package_rel}/ (it has __init__.py). Python "
                        f"imports `{dotted}` from the folder, so {rel} could never be imported. Put the code into "
                        f"{package_rel}/ (e.g. {package_rel}/{path.stem}_main.py, imported from {package_rel}/__init__.py) "
                        f"or into {package_rel}/__init__.py.")
    except Exception as err:
        logger.debug(f"module_shadow_problem failed: {err}")
    return None

# END OF FILE src/utils/module_shadow.py
