#!/usr/bin/env python3
"""
TrippleEffect: fix Jinja2Templates.TemplateResponse calls for Starlette >= 1.0.

Starlette 1.x removed the old signature TemplateResponse(name, {"request": request, ...}).
The first positional argument is now `request`, so the old call passes the dict as the
template name -> "cannot use 'tuple' as a dict key" -> HTTP 500 on GET /.
The keyword form below works on Starlette >= 0.29 and 1.x.

    TemplateResponse("x.html", {"request": request})            -> TemplateResponse(request=request, name="x.html")
    TemplateResponse("x.html", {"request": request, "a": b})    -> TemplateResponse(request=request, name="x.html", context={"a": b})

Scans every .py file under src/. Aborts (writes nothing) if no call is found, or if any
TemplateResponse( call does not match the expected old form.

Usage (repo root):
    python fix_template_response.py --dry-run
    python fix_template_response.py
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path.cwd()

OLD_CALL = re.compile(
    r'TemplateResponse\(\s*'
    r'(?P<name>"[^"\n]+"|\'[^\'\n]+\')\s*,\s*'
    r'\{\s*["\']request["\']\s*:\s*request\s*(?:,\s*(?P<rest>[^{}]*?))?\s*,?\s*\}\s*'
    r'\)'
)
ANY_CALL = re.compile(r'TemplateResponse\(')
NEW_FORM_MARKER = re.compile(r'TemplateResponse\(\s*request\s*=')


def convert(m: re.Match) -> str:
    name, rest = m.group("name"), (m.group("rest") or "").strip().rstrip(",").strip()
    if rest:
        return f"TemplateResponse(request=request, name={name}, context={{{rest}}})"
    return f"TemplateResponse(request=request, name={name})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (ROOT / "src" / "api").is_dir():
        print("ERROR: run this from the TrippleEffect repo root.", file=sys.stderr)
        return 2

    results, problems, total = {}, [], 0
    for path in sorted((ROOT / "src").rglob("*.py")):
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        n_any = len(ANY_CALL.findall(text))
        if not n_any:
            continue
        new_text, n_conv = OLD_CALL.subn(convert, text)
        n_already = len(NEW_FORM_MARKER.findall(text))
        rel = path.relative_to(ROOT)
        if n_conv + n_already != n_any:
            for i, line in enumerate(text.splitlines(), 1):
                if "TemplateResponse(" in line and not OLD_CALL.search(line) and not NEW_FORM_MARKER.search(line):
                    problems.append(f"{rel}:{i}: unrecognised call: {line.strip()}")
        total += n_conv
        if n_conv:
            compile(new_text, str(rel), "exec")
            results[rel] = (text, new_text, n_conv)

    if problems:
        print("ABORTED — nothing was written. These calls need manual review:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1
    if total == 0:
        print("ABORTED — no old-style TemplateResponse(name, {\"request\": ...}) calls found "
              "(already fixed?). Nothing was written.", file=sys.stderr)
        return 1

    for rel, (old, new, n) in results.items():
        if args.dry_run:
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}"))
            print()
        else:
            with open(ROOT / rel, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            print(f"patched {rel} ({n} call(s))")
    print(f"{'DRY RUN — would convert' if args.dry_run else 'Converted'} {total} call(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
