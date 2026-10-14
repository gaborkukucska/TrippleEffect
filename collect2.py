#!/usr/bin/env python3
"""Collect TrippleEffect Round 1 results + Round 2 (auth) files for review."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
DEST = Path.home() / "Desktop" / "te_round2"

FILES = [
    # Round 2: auth hardening (S5, S6, R9)
    "src/api/auth.py",
    "src/api/auth_routes.py",
    "src/api/websocket_manager.py",
    "src/main.py",
    "static/js/auth.js",
    "requirements.txt",
    "run.sh",
    "setup.sh",
    # Package init / prompt context (to check .te_background visibility)
    "src/tools/__init__.py",
    "src/agents/cycle_components/prompt_assembler.py",
]

GREPS = {
    "src/core/database_manager.py": ["class User", "def create_user", "def count_users", "def get_user",
                                     "users", "password_hash", "UNIQUE", "unique=True"],
    "src/api/http_routes.py": ["auth", "Depends", "get_current_user", "SECRET_KEY", "cookie", "Origin"],
    "src/tools/file_system.py": ["startswith('.')", 'startswith(".")', "hidden", "node_modules", "max_depth", "200"],
}

LOG_PATTERNS = re.compile(
    r"PolicyGate|TOOL_EXEC_BLOCKED|sandbox_runner|Policy gate failed|shell sandbox settings|"
    r"command_executor|test_runner|codebase_search|Traceback|CRITICAL|ImportError|AttributeError|TypeError"
)


def flat(rel: str) -> str:
    return rel.replace("/", "__") + ".txt"


def grep_context(target: Path, pattern: str, ctx: int = 6) -> str:
    out = []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"## {target}: read error {e}"
    for i, line in enumerate(lines):
        if pattern in line:
            lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
            out.append(f"## {target.relative_to(ROOT)}:{i + 1}  [{pattern}]")
            out.extend(f"{n + 1:5d}| {lines[n]}" for n in range(lo, hi))
            out.append("")
    return "\n".join(out)


def run(cmd: list, outfile: Path, timeout: int = 900):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        outfile.write_text(f"$ {' '.join(cmd)}\nexit={r.returncode}\n\n--- STDOUT ---\n"
                           f"{r.stdout}\n--- STDERR ---\n{r.stderr}", encoding="utf-8")
    except Exception as e:
        outfile.write_text(f"$ {' '.join(cmd)}\nFAILED TO RUN: {e}\n", encoding="utf-8")


def scan_latest_log(outfile: Path, ctx: int = 3, max_hits: int = 400):
    logs_dir = ROOT / "logs"
    logs = sorted((p for p in logs_dir.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime) if logs_dir.is_dir() else []
    if not logs:
        outfile.write_text("No log files found in logs/.\n", encoding="utf-8")
        return
    latest = logs[-1]
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    out = [f"# Log: {latest.relative_to(ROOT)} ({len(lines)} lines)", ""]
    hits, last_end = 0, -1
    for i, line in enumerate(lines):
        if LOG_PATTERNS.search(line):
            lo, hi = max(0, i - ctx, last_end + 1), min(len(lines), i + ctx + 1)
            if lo > last_end + 1:
                out.append("....")
            out.extend(f"{n + 1:7d}| {lines[n][:600]}" for n in range(lo, hi))
            last_end = hi - 1
            hits += 1
            if hits >= max_hits:
                out.append(f"... stopped after {max_hits} hits")
                break
    out.insert(1, f"# matches: {hits}")
    outfile.write_text("\n".join(out), encoding="utf-8")


def main():
    if not (ROOT / "src" / "agents").is_dir():
        sys.exit("ERROR: run this from the TrippleEffect repo root.")
    if sys.prefix == sys.base_prefix:
        print("WARNING: not running inside a virtualenv; baseline results may be meaningless.")
    DEST.mkdir(parents=True, exist_ok=True)

    missing = []
    for rel in FILES:
        src = ROOT / rel
        if src.is_file():
            shutil.copy2(src, DEST / flat(rel))
        else:
            missing.append(rel)

    dump = []
    for target, patterns in GREPS.items():
        path = ROOT / target
        if not path.is_file():
            dump.append(f"## {target}: MISSING")
            continue
        for p in patterns:
            dump.append(grep_context(path, p))
    (DEST / "zz_grep_context.txt").write_text("\n".join(dump), encoding="utf-8")

    py = sys.executable
    run([py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE"], DEST / "zz_pytest_full.txt")
    run([py, "-m", "pyflakes", "src"], DEST / "zz_pyflakes.txt")
    run([py, "--version"], DEST / "zz_python_version.txt")
    run([py, "-m", "pip", "freeze"], DEST / "zz_pip_freeze.txt")
    run(["git", "log", "--oneline", "-5"], DEST / "zz_git_log.txt")
    run(["git", "status", "--short"], DEST / "zz_git_status.txt")
    scan_latest_log(DEST / "zz_log_scan.txt")

    print(f"Copied {len(FILES) - len(missing)} files to {DEST}")
    if missing:
        print("MISSING:", *missing, sep="\n  ")


if __name__ == "__main__":
    main()
