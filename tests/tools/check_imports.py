#!/usr/bin/env python3
"""
TrippleEffect: import every module under src/ and report which ones fail,
plus the installed versions of the key dependencies.

Run from the repo root with the venv Python:
    .venv/bin/python check_imports.py
Writes ~/Desktop/te_import_check.txt and prints a short summary.
"""
import importlib
import importlib.metadata as md
import io
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path.cwd()
OUT = Path.home() / "Desktop" / "te_import_check.txt"

KEY_PACKAGES = [
    "openai", "httpx", "httpx2", "SQLAlchemy", "greenlet", "aiosqlite", "fastapi", "starlette",
    "uvicorn", "pydantic", "bcrypt", "PyJWT", "aiohttp", "websockets", "tasklib", "taskw",
    "python-dotenv", "PyYAML", "GitPython", "libzim", "netifaces2", "python-nmap", "beautifulsoup4",
]


def main() -> int:
    if not (ROOT / "src" / "agents").is_dir():
        print("ERROR: run this from the TrippleEffect repo root.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))

    lines = [f"Python: {sys.version}", f"Executable: {sys.executable}",
             f"In venv: {sys.prefix != sys.base_prefix}", "", "## Installed versions"]
    for pkg in KEY_PACKAGES:
        try:
            lines.append(f"  {pkg:16s} {md.version(pkg)}")
        except md.PackageNotFoundError:
            lines.append(f"  {pkg:16s} NOT INSTALLED")

    modules = sorted(
        ".".join(p.relative_to(ROOT).with_suffix("").parts)
        for p in (ROOT / "src").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    modules = [m[: -len(".__init__")] if m.endswith(".__init__") else m for m in modules]

    ok, failed = [], []
    noise = io.StringIO()
    for mod in modules:
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                importlib.import_module(mod)
            ok.append(mod)
        except BaseException as e:  # noqa: BLE001 - we want every failure, incl. SystemExit
            tb = traceback.format_exception(type(e), e, e.__traceback__)
            failed.append((mod, f"{type(e).__name__}: {e}", "".join(tb[-6:])))

    lines += ["", f"## Imported OK: {len(ok)} / {len(modules)}"]
    lines += [f"  {m}" for m in ok]
    lines += ["", f"## FAILED: {len(failed)}"]
    for mod, short, tb in failed:
        lines += [f"### {mod}", f"  {short}", tb, ""]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Imported OK: {len(ok)}/{len(modules)}   FAILED: {len(failed)}")
    for mod, short, _ in failed:
        print(f"  FAIL {mod}: {short[:160]}")
    print(f"Full report: {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
