# START OF FILE tests/unit/test_round5j.py
"""
Round 5j: the run of 2026-11-18 20:07 .. 20:24 (WiFi_Multiplayer_Snake_Game, qwen3.5:4b; the laptop switched off).

1. 20:14-20:18 W1 was told "'random' is used on line 266 but is never imported (missing 'import random'?)" and sent
   code_editor replace_function for 'index' with its UNCHANGED code six times ("I need to add the missing import random
   ... to the imports section"), each answered "Successfully applied 1 edits", until it was moved to worker_report.
2. 20:08 the kick-off kept public/html/index.html and public/css/styles.css beside the tasks' public/index.html and
   public/styles.css: W2 wrote the page twice.
"""
import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path

import src.agents  # noqa: F401 - resolves pre-existing circular import order


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


SERVER = ("import json\n\n\n@app.route('/')\ndef index():\n    return 'hi'\n\n\n"
          "def food():\n    return random.randint(0, 19)\n")


def _edit(ws: Path, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute(agent_id="W1", agent_sandbox_path=ws, action="replace_function",
                                            filename="server.py", **kwargs))


def test_a_definition_sent_unchanged_changes_nothing_and_says_so(tmp_path):
    (tmp_path / "server.py").write_text(SERVER, encoding="utf-8")
    result = _edit(tmp_path, function="index", code="@app.route('/')\ndef index():\n    return 'hi'\n")
    assert result["status"] == "success"
    assert result["message"].startswith("Nothing changed in 'server.py': your code for 'index' is EXACTLY what lines 4-6 "
                                        "already hold, so this call did nothing.")
    assert "[Framework check - still open in this file:]" in result["message"]
    assert "'random' is used on line 10 but is never imported" in result["message"]
    assert ("To add the import line(s): code_editor action 'replace_function' with filename 'server.py' and code "
            "'import random' - import lines need no 'function'") in result["message"]
    assert (tmp_path / "server.py").read_text(encoding="utf-8") == SERVER
    # a real change is still applied, with the same how-to
    changed = _edit(tmp_path, function="index", code="@app.route('/')\ndef index():\n    return 'hello'\n")
    assert changed["message"].startswith("Successfully applied 1 edits to 'server.py'.")
    assert "code 'import random'" in changed["message"]
    # and the how-to works: import lines alone are merged
    added = _edit(tmp_path, code="import random")
    assert added["status"] == "success" and "import random" in (tmp_path / "server.py").read_text(encoding="utf-8")
    assert "'random' is used" not in added["message"]


def test_the_import_howto():
    from src.tools.python_static_check import missing_import_howto, saved_edit_notes
    notes = ["'random' is used on line 3 but is never imported or defined in this file (missing 'import random'?).",
             "'Decimal' is used on line 5 but is never imported or defined in this file, so it raises NameError "
             "(missing 'from decimal import Decimal'?).",
             "function 'f' (line 9) contains only a docstring and no code: its body is not implemented yet."]
    assert missing_import_howto(notes, "src/a.py") == (
        "\nTo add the import line(s): code_editor action 'replace_function' with filename 'src/a.py' and code "
        "'import random\\nfrom decimal import Decimal' - import lines need no 'function' and are merged into the file's "
        "imports. (Sending a function's code again does not add an import.)")
    assert missing_import_howto(notes[2:], "src/a.py") == ""
    assert saved_edit_notes("def f():\n    return os.getcwd()\n", "src/a.py").endswith(
        "(Sending a function's code again does not add an import.)")


def test_a_shared_name_in_a_type_folder_beside_the_tasks_file_is_the_tasks_file():
    from src.workflows.pm_kickoff_workflow import duplicate_structure_files
    drop, notes = duplicate_structure_files(
        ["public/html/index.html", "public/css/styles.css", "public/js/client.js", "src/server/server.py"],
        ["public/index.html", "public/styles.css", "public/client.js", "src/server/server.py"])
    assert drop == ["public/html/index.html", "public/css/styles.css", "public/js/client.js"]
    assert notes[0] == ("Planned file 'public/html/index.html' dropped: the kick-off task names this file "
                        "'public/index.html', so that is the one path to create.")
    # a type folder somewhere else, Flask's templates/ and other shared names stay
    assert duplicate_structure_files(["static/css/style.css"], ["public/style.css"]) == ([], [])
    assert duplicate_structure_files(["public/templates/index.html"], ["public/index.html"]) == ([], [])
    assert duplicate_structure_files(["public/index.html"], ["src/index.html"]) == ([], [])

# END OF FILE tests/unit/test_round5j.py
