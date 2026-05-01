"""Recursively validate every texts/*.md file against doc/format.md.

Exit code 0 when all files pass; 1 if any file has errors. Designed for CI.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

from tools.segment import (
    FormatError,
    file_segments_sha256,
    parse_file,
)

REQUIRED_TOP_FIELDS = (
    "work", "work_title", "book", "book_title", "juan", "title",
    "author", "script", "source", "segments_sha256",
)
REQUIRED_SOURCE_FIELDS = ("id", "url", "retrieved", "sha256")
ALLOWED_SCRIPTS = {"traditional", "simplified"}
KNOWN_SOURCE_IDS = {"ctext", "zhonghua1959", "bona", "wuying"}


def walk_texts(root: Path) -> Iterator[Path]:
    """Yield every .md file under root in deterministic order."""
    yield from sorted(p for p in root.rglob("*.md") if p.is_file())


def _check_frontmatter(fm: dict) -> list[str]:
    errs: list[str] = []
    for k in REQUIRED_TOP_FIELDS:
        if k not in fm:
            errs.append(f"frontmatter missing required field: {k!r}")
    if "script" in fm and fm["script"] not in ALLOWED_SCRIPTS:
        errs.append(f"frontmatter.script={fm['script']!r}, expected one of {sorted(ALLOWED_SCRIPTS)}")
    if "juan" in fm and not isinstance(fm["juan"], int):
        errs.append(f"frontmatter.juan must be an integer, got {type(fm['juan']).__name__}")
    src = fm.get("source")
    if src is None:
        return errs
    if not isinstance(src, dict):
        errs.append(f"frontmatter.source must be a mapping, got {type(src).__name__}")
        return errs
    for k in REQUIRED_SOURCE_FIELDS:
        if k not in src:
            errs.append(f"frontmatter.source missing required field: {k!r}")
    if src.get("id") and src["id"] not in KNOWN_SOURCE_IDS:
        errs.append(f"frontmatter.source.id={src['id']!r} is unknown; add to KNOWN_SOURCE_IDS in tools/check.py")
    if src.get("sha256") and not re.fullmatch(r"[0-9a-f]{64}", str(src["sha256"])):
        errs.append(f"frontmatter.source.sha256 is not a 64-char lowercase hex sha256")
    return errs


def _check_segment_ids(fm: dict, segments) -> list[str]:
    errs: list[str] = []
    book = fm.get("book")
    juan = fm.get("juan")
    if not isinstance(book, str) or not isinstance(juan, int):
        return errs  # already reported by _check_frontmatter
    expected_prefix = f"{book}.{juan}.p"
    seg_id_re = re.compile(rf"^{re.escape(book)}\.{juan}\.p\d+[a-z]*$")
    for s in segments:
        if not seg_id_re.fullmatch(s.id):
            errs.append(
                f"segment id {s.id!r} does not match expected pattern {expected_prefix}<N>[<a-z>]"
            )
    return errs


def _check_segments_hash(fm: dict, segments) -> list[str]:
    if "segments_sha256" not in fm:
        return []
    expected = fm["segments_sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return [f"segments_sha256 must be a 64-char lowercase hex string, got {expected!r}"]
    actual = file_segments_sha256(segments)
    if expected != actual:
        return [f"segments_sha256 mismatch: frontmatter={expected} actual={actual}"]
    return []


def validate_text_file(path: Path) -> list[str]:
    """Return a list of error strings; empty list means the file is valid."""
    try:
        parsed = parse_file(path)
    except FormatError as e:
        return [f"parse error: {e}"]
    except Exception as e:  # noqa: BLE001 — surface as error
        return [f"unexpected error: {e!r}"]
    errs = _check_frontmatter(parsed.frontmatter)
    errs += _check_segment_ids(parsed.frontmatter, parsed.segments)
    errs += _check_segments_hash(parsed.frontmatter, parsed.segments)
    return errs


def run(roots: Iterable[Path]) -> tuple[int, int]:
    """Validate every .md under each root. Returns (n_files, n_failed)."""
    n_files = 0
    n_failed = 0
    for root in roots:
        if not root.exists():
            print(f"WARN: path does not exist: {root}", file=sys.stderr)
            continue
        if root.is_file():
            files: list[Path] = [root]
        else:
            files = list(walk_texts(root))
        for f in files:
            n_files += 1
            errs = validate_text_file(f)
            if errs:
                n_failed += 1
                print(f"FAIL {f}")
                for e in errs:
                    print(f"  - {e}")
            else:
                print(f"OK   {f}")
    return n_files, n_failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate texts/ files against doc/format.md.")
    p.add_argument("paths", nargs="*", type=Path, default=[Path("texts")],
                   help="files or directories to check (default: texts/)")
    args = p.parse_args(argv)
    n_files, n_failed = run(args.paths)
    print(f"\n{n_files} file(s) checked, {n_failed} failed")
    return 1 if n_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
