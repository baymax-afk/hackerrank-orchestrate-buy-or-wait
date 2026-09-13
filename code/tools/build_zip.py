"""Build code.zip for submission (cross-platform, deterministic ordering, secret guard).

    python code/tools/build_zip.py            # -> ./code.zip
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INCLUDE_DIRS = ["code", "tests", "research"]
INCLUDE_FILES = ["README.md", "requirements.txt", ".env.example", "pytest.ini", "problem_statement.md"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", "traces", ".tmp"}
EXCLUDE_SUFFIXES = {".pyc", ".tmp"}
SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")


def iter_files():
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_dir() or any(part in EXCLUDE_PARTS for part in p.parts) or p.suffix in EXCLUDE_SUFFIXES:
                continue
            yield p
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists():
            yield p


def main() -> int:
    out = ROOT / "code.zip"
    files = list(iter_files())
    for p in files:
        if p.suffix in (".py", ".md", ".txt", ".json", ".jsonl", ".example", ".ini"):
            if SECRET_PATTERN.search(p.read_text(encoding="utf-8", errors="ignore")):
                print(f"refusing to package {p}: looks like it contains an API key", file=sys.stderr)
                return 1
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT).as_posix())
    print(f"wrote {out} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
