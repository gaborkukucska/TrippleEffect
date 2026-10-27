# START OF FILE src/utils/framework_conftest.py
"""
The framework-owned root conftest.py of a Python project (Round 1u; Round 3r: one module per file).

Round 1u: the root conftest.py puts src/ and the project root on sys.path, so tests write `from my_module import ...`.
Round 3r: 2026-10-26 (WiFi_Snake_Game, qwen3.5:4b) tests/test_snake_game.py imported `from src.player_manager import
PlayerManager` and `from src.api import register_player`, while src/api.py imported `from player_manager import
PlayerManager`. Python loaded src/player_manager.py TWICE - as 'src.player_manager' and as 'player_manager' - so the test's
PlayerManager and the one src/api.py used were two different classes with separate data: resetting one in a fixture left the
other as it was. W2 rewrote its tests for 85 minutes. The conftest now makes `src.x` the module `x` (src/x.py is loaded
once), unless src/ is a package of its own (src/__init__.py) or the module cannot be loaded as `x` (relative imports).

* FRAMEWORK_CONFTEST: the current text. FRAMEWORK_CONFTEST_MARKER: the words that mark the file as the framework's.
* refresh_framework_conftest(workspace): replaces a root conftest.py whose text is EXACTLY an earlier framework version with
  the current one (anything else - a project's own conftest - is left alone). Never raises.
* Round 3y: framework_conftest_change_error(workspace, rel): the refusal for ANY agent change of the framework-owned root
  conftest.py (file_system write/edit/delete/move and code_editor - only 'write' was refused before). 2026-10-27 13:44:44
  W4 changed `type(finder).__name__` to `type(finder).__name` with code_editor; from then on every pytest run of the project
  stopped with "AttributeError: type object 'AssertionRewritingHook' has no attribute '__name'" while loading conftest.py.
  refresh_framework_conftest() also restores a framework-owned conftest.py that an AGENT changed (recorded by the tool
  executor, src/utils/file_authorship.py).
"""
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

FRAMEWORK_CONFTEST_MARKER = "FRAMEWORK-OWNED conftest"
# Round 1u - Round 3q
FRAMEWORK_CONFTEST_1U = "\n".join([
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
FRAMEWORK_CONFTEST = "\n".join([
    '"""FRAMEWORK-OWNED conftest: makes modules in src/ and in the project root importable from the tests.',
    '',
    'Do not edit or recreate this file. In tests simply write `from my_module import my_function`.',
    '`import src.my_module` and `import my_module` give the SAME module: src/my_module.py is loaded once.',
    '"""',
    'import importlib',
    'import importlib.abc',
    'import importlib.util',
    'import sys',
    'from pathlib import Path',
    '',
    '_ROOT = Path(__file__).resolve().parent',
    'for _path in (_ROOT / "src", _ROOT):',
    '    if _path.is_dir() and str(_path) not in sys.path:',
    '        sys.path.insert(0, str(_path))',
    '',
    '',
    'class _OneModulePerFile(importlib.abc.MetaPathFinder, importlib.abc.Loader):',
    '    """`src.x` is the module `x`: otherwise Python loads src/x.py twice, with two copies of its classes and data."""',
    '',
    '    def __init__(self):',
    '        self._specs = {}',
    '',
    '    def find_spec(self, name, path=None, target=None):',
    '        if not name.startswith("src.") or (_ROOT / "src" / "__init__.py").exists():',
    '            return None',
    '        short = name[len("src."):]',
    '        base = _ROOT / "src" / Path(*short.split("."))',
    '        files = {Path(str(base) + ".py").resolve(), (base / "__init__.py").resolve()}',
    '        if not any(file.is_file() for file in files):',
    '            return None',
    '        try:',
    '            module = importlib.import_module(short)',
    '        except Exception:',
    '            return None  # loaded the usual way as src.x (e.g. a module with relative imports)',
    '        if not getattr(module, "__file__", None) or Path(module.__file__).resolve() not in files:',
    '            return None',
    '        self._specs[name] = getattr(module, "__spec__", None)',
    '        return importlib.util.spec_from_loader(name, self, origin=module.__file__)',
    '',
    '    def create_module(self, spec):',
    '        return sys.modules[spec.name[len("src."):]]',
    '',
    '    def exec_module(self, module):',
    '        original = self._specs.pop(module.__spec__.name, None)',
    '        if original is not None:',
    '            module.__spec__ = original  # the module keeps its own name and spec',
    '',
    '',
    'if not any(type(finder).__name__ == "_OneModulePerFile" for finder in sys.meta_path):',
    '    sys.meta_path.insert(0, _OneModulePerFile())',
    '',
])
EARLIER_FRAMEWORK_CONFTESTS = (FRAMEWORK_CONFTEST_1U,)


def refresh_framework_conftest(workspace: Any) -> bool:
    """Round 3r: True if the workspace's root conftest.py was an earlier framework version and now is the current one."""
    try:
        if not workspace:
            return False
        conftest = Path(workspace) / "conftest.py"
        if not conftest.is_file():
            return False
        text = conftest.read_text(encoding="utf-8")
        if text == FRAMEWORK_CONFTEST:
            return False
        if text not in EARLIER_FRAMEWORK_CONFTESTS:
            # Round 3y: a framework-owned conftest.py that an agent changed is the framework's again
            from src.utils.file_authorship import last_writer
            writer = last_writer(conftest)
            if not writer or FRAMEWORK_CONFTEST_MARKER not in text[:300]:
                return False
            conftest.write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
            from src.utils.file_authorship import forget_file
            forget_file(conftest)
            logger.warning(f"framework_conftest: restored the framework-owned {conftest} that '{writer}' had changed.")
            return True
        conftest.write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
        logger.info(f"framework_conftest: updated the framework-owned {conftest} to the current version.")
        return True
    except Exception as err:
        logger.debug(f"refresh_framework_conftest failed: {err}")
        return False


CHANGE_REFUSAL = ("conftest.py is managed by the framework and must not be changed. Modules in src/ and in the project root "
                  "are already importable from tests: write `from my_module import my_function`. Run tests with "
                  "`python -m pytest -q` from the workspace root. If a test cannot import something, fix the TEST or the "
                  "module - never conftest.py.")


def framework_conftest_change_error(workspace: Any, rel_path: Any) -> Optional[str]:
    """Round 3y: the refusal for a change of the framework-owned ROOT conftest.py of the workspace, else None."""
    try:
        if not workspace or not rel_path:
            return None
        rel = str(rel_path).replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        if rel.lstrip("/") != "conftest.py":
            return None
        conftest = Path(workspace) / "conftest.py"
        if not conftest.is_file():
            return None
        if FRAMEWORK_CONFTEST_MARKER not in conftest.read_text(encoding="utf-8", errors="replace")[:300]:
            return None  # a project's own conftest.py
        return CHANGE_REFUSAL
    except Exception as err:
        logger.debug(f"framework_conftest_change_error failed: {err}")
        return None

# END OF FILE src/utils/framework_conftest.py
