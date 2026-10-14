#!/usr/bin/env python3
"""Collect TrippleEffect files + baseline diagnostics for review round 1."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
DEST = Path.home() / "Desktop" / "te_round1"

FILES = [
    # Rules / project state
    "helperfiles/DEVELOPMENT_RULES.md",
    "helperfiles/CURRENT_ISSUES.md",
    ".gitignore",
    "requirements.txt",
    "pyproject.toml",
    ".env.example",
    "governance.yaml",
    # Execution path (S1, S2, S3, S7, R3, R4, R10, R11)
    "src/tools/command_executor.py",
    "src/tools/test_runner.py",
    "src/tools/codebase_search.py",
    "src/tools/executor.py",
    "src/tools/base.py",
    "src/tools/error_handler.py",
    "src/agents/interaction_handler.py",
    "src/agents/core.py",
    "src/config/settings.py",
]

GREPS = {
    # file: list of patterns -> context dump so I don't need the 3k-line file yet
    "src/agents/cycle_handler.py": [
        "has_command_execution", "_get_cg_verdict", "execute_single_tool",
        "command_executor", "DISABLE_COMMAND_EXECUTION", "tool_executor",
    ],
    "src/agents/cycle_components/agent_health_monitor.py": ["command_executor", "GP008"],
    "src/": ["os.environ.copy", "create_subprocess_shell", "create_subprocess_exec",
             "preexec_fn", "subprocess.run", "subprocess.Popen", "DISABLE_COMMAND_EXECUTION"],
}


def flat(rel: str) -> str:
    return rel.replace("/", "__") + ".txt"


def grep_context(target: Path, pattern: str, ctx: int = 6) -> str:
    out = []
    files = [target] if target.is_file() else sorted(target.rglob("*.py"))
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            out.append(f"## {f}: read error {e}")
            continue
        for i, line in enumerate(lines):
            if pattern in line:
                lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
                out.append(f"## {f.relative_to(ROOT)}:{i + 1}  [{pattern}]")
                out.extend(f"{n + 1:5d}| {lines[n]}" for n in range(lo, hi))
                out.append("")
    return "\n".join(out)


def run(cmd: list, outfile: Path):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        outfile.write_text(f"$ {' '.join(cmd)}\nexit={r.returncode}\n\n--- STDOUT ---\n"
                           f"{r.stdout}\n--- STDERR ---\n{r.stderr}", encoding="utf-8")
    except Exception as e:
        outfile.write_text(f"$ {' '.join(cmd)}\nFAILED TO RUN: {e}\n", encoding="utf-8")


def main():
    if not (ROOT / "src" / "agents").is_dir():
        sys.exit("ERROR: run this from the TrippleEffect repo root.")
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
        for p in patterns:
            dump.append(grep_context(ROOT / target, p))
    (DEST / "zz_grep_context.txt").write_text("\n".join(dump), encoding="utf-8")

    py = sys.executable
    run([py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--tb=line"],
        DEST / "zz_pytest_baseline.txt")
    run([py, "-m", "pyflakes", "src"], DEST / "zz_pyflakes_baseline.txt")
    run([py, "--version"], DEST / "zz_python_version.txt")
    run([py, "-m", "pip", "freeze"], DEST / "zz_pip_freeze.txt")

    print(f"Copied {len(FILES) - len(missing)} files to {DEST}")
    if missing:
        print("MISSING:", *missing, sep="\n  ")


if __name__ == "__main__":
    main()
