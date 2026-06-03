#!/usr/bin/env python3
"""README example linter.

Catches the post-PEP-668 venv-form footgun: README code blocks that say
`python3 <script>.py ...` will hit `ModuleNotFoundError: No module named
'gungnir'` if the user followed setup.sh (which puts deps in .venv/).
Muninn shipped this regression on 2026-06-01 when the Pi24 user followed
the README literally; this linter is the safety net so it doesn't repeat.

What this checks:

- Every fenced ```bash code block in README.md is scanned line by line.
- Any line that invokes `python3 <script>.py` (or `python <script>.py`)
  directly is flagged UNLESS one of these escape hatches applies:
    1. The line is inside an "Option B - clone with git" or similar
       manual-install block, AND the block also references
       `python3 -m venv .venv` or `.venv/bin/`. Those are intentional
       teaching examples — they show the system-python form alongside
       the venv answer.
    2. The line is preceded by a comment containing `# direct invocation`
       (explicit author override).

The script name is auto-detected from the repo (it's the only top-level
`.py` file that isn't an obvious helper). Pass `--script NAME.py` to
override.

Run: python scripts/check_readme_examples.py [README.md]
Exit codes: 0 clean, 1 issues found, 2 setup error.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path


CODE_FENCE = re.compile(r"^```(\w+)?\s*$")
VENV_FORM = re.compile(r"(\.venv/bin/python|python3?\s+-m\s+venv)")
OVERRIDE_MARKER = "# direct invocation"

# Helpers that aren't the main entrypoint, so auto-detect skips them.
_AUX_SCRIPTS = {"setup.py", "conftest.py"}


def _autodetect_script(repo_root: Path) -> str | None:
    candidates = [
        p.name for p in repo_root.glob("*.py")
        if p.name not in _AUX_SCRIPTS and not p.name.startswith("test_")
        and not p.name.startswith("_")
    ]
    if len(candidates) == 1:
        return candidates[0]
    # Prefer the one whose name matches the repo dir, if any.
    repo_name = repo_root.name.replace("-", "_")
    for c in candidates:
        if c == f"{repo_name}.py":
            return c
    return None


def lint_readme(
    path: Path, script: str
) -> list[tuple[int, str, str]]:
    """Return a list of (line_number, line, why) findings."""
    problem = re.compile(rf"^\s*(python3?)\s+{re.escape(script)}\b")
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[tuple[int, str, str]] = []
    in_block = False
    block_lang = ""
    block_lines: list[tuple[int, str]] = []

    def flush_block():
        if not block_lines:
            return
        block_text = "\n".join(l for _, l in block_lines)
        teaching = bool(VENV_FORM.search(block_text))
        prev_override = False
        for lineno, line in block_lines:
            if OVERRIDE_MARKER in line:
                prev_override = True
                continue
            if problem.match(line):
                if teaching or prev_override:
                    prev_override = False
                    continue
                findings.append((
                    lineno, line,
                    "uses system python3 directly; either prefix with "
                    "`.venv/bin/python` / `./run.sh`, or move the line "
                    "into a teaching block that explains `python3 -m "
                    f"venv .venv`, or annotate with `{OVERRIDE_MARKER}`",
                ))
            prev_override = False

    for i, line in enumerate(lines, start=1):
        m = CODE_FENCE.match(line)
        if m:
            if in_block:
                flush_block()
                in_block = False
                block_lines = []
            else:
                in_block = True
                block_lang = (m.group(1) or "").lower()
                block_lines = []
            continue
        if in_block and block_lang in ("bash", "sh", "shell", ""):
            block_lines.append((i, line))
    if in_block:
        flush_block()
    return findings


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("readme", nargs="?", default="README.md",
                    help="Path to README (default: README.md)")
    ap.add_argument("--script", default=None,
                    help="Main script name (auto-detected by default)")
    args = ap.parse_args(argv[1:])

    readme = Path(args.readme)
    if not readme.exists():
        print(f"{readme}: not found", file=sys.stderr)
        return 2

    repo_root = readme.parent.resolve()
    script = args.script or _autodetect_script(repo_root)
    if not script:
        print(
            f"could not auto-detect main script in {repo_root}; "
            "pass --script NAME.py",
            file=sys.stderr,
        )
        return 2

    findings = lint_readme(readme, script)
    if not findings:
        print(
            f"{readme}: clean (script={script}, "
            f"{len(readme.read_text(encoding='utf-8').splitlines())} lines)"
        )
        return 0
    print(
        f"{readme}: {len(findings)} issue(s) found (script={script})",
        file=sys.stderr,
    )
    for lineno, line, why in findings:
        print(f"  {readme}:{lineno}: {line.strip()}", file=sys.stderr)
        print(f"    {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
