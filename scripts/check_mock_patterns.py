#!/usr/bin/env python
"""
Fail (exit 1) if mock / placeholder data patterns appear in application code.

Scans git-tracked files only. Test code, the shared test library, docs and
markdown are allowed to contain sample data and are skipped.

Usage:
    python scripts/check_mock_patterns.py            # scan all tracked files
    python scripts/check_mock_patterns.py FILE...    # scan given files (pre-commit)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PATTERNS: dict[str, re.Pattern[str]] = {
    "test@": re.compile(r"test@", re.IGNORECASE),
    "example.com": re.compile(r"example\.com", re.IGNORECASE),
    "foo": re.compile(r"\bfoo\b", re.IGNORECASE),
    "bar": re.compile(r"\bbar\b", re.IGNORECASE),
    "dummy": re.compile(r"dummy", re.IGNORECASE),
    "fake": re.compile(r"\bfake", re.IGNORECASE),
    "mock": re.compile(r"\bmock", re.IGNORECASE),
    "lorem": re.compile(r"lorem", re.IGNORECASE),
    "TODO remove": re.compile(r"TODO\s*:?\s*remove", re.IGNORECASE),
    "placeholder secret": re.compile(r"change-me-in-production"),
}

# Path segments / suffixes where sample data is legitimate.
EXCLUDED_DIRS = {"tests", "docs", "scripts", ".venv", "node_modules", ".idea", ".github"}
EXCLUDED_PREFIXES = ("libs/testing/",)
EXCLUDED_FILES = {"scripts/check_mock_patterns.py", ".pre-commit-config.yaml"}
EXCLUDED_SUFFIXES = {".md", ".pyc", ".png", ".jpg", ".ico", ".lock", ".yml", ".yaml", ".example", ".sh", ".ps1"}


def is_excluded(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    posix = p.as_posix()
    if posix in EXCLUDED_FILES or posix.startswith(EXCLUDED_PREFIXES):
        return True
    if p.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_DIRS for part in p.parts)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def scan(paths: list[str]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        if is_excluded(path) or not Path(path).is_file():
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "# noqa" in line:
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    problems.append(f"{path}:{lineno}: [{name}] {line.strip()}")
    return problems


def main(argv: list[str]) -> int:
    paths = argv or tracked_files()
    problems = scan(paths)
    if problems:
        print("Mock/placeholder data found outside tests/ (move it to tests/ or libs/testing/):")
        print("\n".join(problems))
        return 1
    print(f"OK - no mock data patterns in {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
