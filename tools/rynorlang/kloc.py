#!/usr/bin/env python3
"""Stage 19d kLOC budget counter (host-side, stdlib only).

Counting rule (frozen by docs/design/rynorlang-conformance.md §7):
non-blank, non-full-line-comment lines in the counted sources.
Deterministic: directory walks are sorted; output is plain text.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def count_lines(path: str | Path, comment_prefix: str = "//") -> int:
    """Count effective lines in one file (never raises on bad input)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 0
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(comment_prefix):
            continue
        total += 1
    return total


def count_tree(root: str | Path, suffix: str = ".rl", comment_prefix: str = "//"):
    """Count one file or directory tree. Returns (files, lines)."""
    base = Path(root)
    if base.is_file():
        return (1, count_lines(base, comment_prefix))
    files = sorted(p for p in base.rglob(f"*{suffix}") if p.is_file())
    return (len(files), sum(count_lines(p, comment_prefix) for p in files))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="files or directories to count")
    parser.add_argument("--suffix", default=".rl", help="file suffix for directories")
    parser.add_argument("--comment-prefix", default="//", help="full-line comment marker")
    args = parser.parse_args(argv)
    total_files = 0
    total_lines = 0
    for raw in args.paths:
        files, lines = count_tree(raw, args.suffix, args.comment_prefix)
        print(f"{raw}: {files} file(s), {lines} line(s)")
        total_files += files
        total_lines += lines
    print(f"total: {total_files} file(s), {total_lines} line(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
