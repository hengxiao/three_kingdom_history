"""Recursively validate texts/*.md and annotations/*.yaml against doc/format.md.

Exit code 0 when all files pass; 1 if any file has errors. Designed for CI.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

import yaml

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
KNOWN_SOURCE_IDS = {"wikisource", "ctext", "zhonghua1959", "bona", "wuying"}

REQUIRED_ANNOTATIONS_FILE_FIELDS = ("chapter", "source", "annotations")
REQUIRED_ANNOTATION_FIELDS = ("id", "anchor", "at", "length", "type", "text")
ALLOWED_ANNOTATION_TYPES = {"pei", "lixian", "chen", "editor", "crossref", "temporal"}
REQUIRED_TEMPORAL_FIELDS = ("year_ad",)
ALLOWED_TEMPORAL_RESOLUTIONS = {
    "absolute", "bare_year", "bare_month", "this_year", "next_year", "prev_year",
}
# Annotation IDs use a single-letter tag prefix per type series (a for pei/chen/editor/crossref, t for temporal).
_ANN_ID_RE = re.compile(r"^([a-z]+)\.(\d+)\.p\d+[a-z]*\.[a-z]\d+$")
_CHAPTER_ID_RE = re.compile(r"^([a-z]+)\.(\d+)$")


def walk_texts(root: Path) -> Iterator[Path]:
    """Yield every .md file under root in deterministic order."""
    yield from sorted(p for p in root.rglob("*.md") if p.is_file())


def walk_annotations(root: Path) -> Iterator[Path]:
    """Yield every .yaml file under root in deterministic order."""
    yield from sorted(p for p in root.rglob("*.yaml") if p.is_file())


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


def _segments_for_chapter(chapter_id: str, repo_root: Path) -> dict[str, int] | None:
    """Look up the texts/ file for `<book>.<juan>` and return {seg_id: canonical_len}, or None if missing."""
    m = _CHAPTER_ID_RE.match(chapter_id)
    if not m:
        return None
    book, juan = m.group(1), int(m.group(2))
    # Layout differs by work: sanguozhi nests by book, 后汉书 is flat under houhanshu/.
    if book == "hhs":
        text_path = repo_root / "texts" / "houhanshu" / f"{juan:02d}.md"
    else:
        text_path = repo_root / "texts" / "sanguozhi" / book / f"{juan:02d}.md"
    if not text_path.exists():
        return None
    try:
        parsed = parse_file(text_path)
    except FormatError:
        return None
    return {s.id: len(s.text) for s in parsed.segments}


def validate_annotation_file(path: Path, *, repo_root: Path) -> list[str]:
    """Validate annotations/<…>.yaml per doc/format.md §5."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]
    if not isinstance(doc, dict):
        return ["top-level must be a YAML mapping"]
    errs: list[str] = []
    for k in REQUIRED_ANNOTATIONS_FILE_FIELDS:
        if k not in doc:
            errs.append(f"missing required field: {k!r}")
    if errs:
        return errs

    chapter_id = doc["chapter"]
    if not isinstance(chapter_id, str) or not _CHAPTER_ID_RE.match(chapter_id):
        errs.append(f"chapter must be '<book>.<juan>' form, got {chapter_id!r}")

    # source block
    src = doc.get("source")
    if not isinstance(src, dict):
        errs.append("source must be a mapping")
    else:
        for k in REQUIRED_SOURCE_FIELDS:
            if k not in src:
                errs.append(f"source missing required field: {k!r}")
        if src.get("id") and src["id"] not in KNOWN_SOURCE_IDS:
            errs.append(f"source.id={src['id']!r} unknown")
        if src.get("sha256") and not re.fullmatch(r"[0-9a-f]{64}", str(src["sha256"])):
            errs.append("source.sha256 not a 64-char lowercase hex sha256")

    # annotations list
    anns = doc.get("annotations")
    if not isinstance(anns, list):
        errs.append("annotations must be a list")
        return errs

    seg_lengths = _segments_for_chapter(str(chapter_id), repo_root) or {}
    seen_ids: set[str] = set()
    for i, a in enumerate(anns):
        if not isinstance(a, dict):
            errs.append(f"annotations[{i}] is not a mapping")
            continue
        for k in REQUIRED_ANNOTATION_FIELDS:
            if k not in a:
                errs.append(f"annotations[{i}] missing field: {k!r}")
        ann_id = a.get("id")
        if ann_id is not None and not _ANN_ID_RE.match(str(ann_id)):
            errs.append(f"annotation id {ann_id!r} does not match '<book>.<juan>.p<N>.a<M>' pattern")
        if ann_id in seen_ids:
            errs.append(f"duplicate annotation id: {ann_id}")
        seen_ids.add(ann_id)
        if a.get("type") not in ALLOWED_ANNOTATION_TYPES:
            errs.append(f"annotations[{i}].type={a.get('type')!r} not in {sorted(ALLOWED_ANNOTATION_TYPES)}")
        anchor = a.get("anchor")
        if seg_lengths and anchor:
            if anchor not in seg_lengths:
                errs.append(f"annotation {ann_id!r} anchor {anchor!r} not in texts/ segments")
            else:
                at = a.get("at")
                if not isinstance(at, int) or at < 0 or at > seg_lengths[anchor]:
                    errs.append(
                        f"annotation {ann_id!r} at={at!r} out of range [0, {seg_lengths[anchor]}]"
                    )
                length = a.get("length")
                if not isinstance(length, int) or length < 0:
                    errs.append(f"annotation {ann_id!r} length={length!r} must be a non-negative integer")
        text = a.get("text")
        if text is not None and not isinstance(text, str):
            errs.append(f"annotation {ann_id!r} text must be a string")
        if a.get("type") == "temporal":
            for k in REQUIRED_TEMPORAL_FIELDS:
                if k not in a:
                    errs.append(f"temporal annotation {ann_id!r} missing field: {k!r}")
            era_year = a.get("era_year")
            year_ad = a.get("year_ad")
            if era_year is not None and (not isinstance(era_year, int) or era_year < 1):
                errs.append(f"temporal annotation {ann_id!r} era_year must be a positive integer")
            if year_ad is not None and (not isinstance(year_ad, int) or year_ad < 1):
                errs.append(f"temporal annotation {ann_id!r} year_ad must be a positive integer")
            month_ordinal = a.get("month_ordinal")
            if month_ordinal is not None and (
                not isinstance(month_ordinal, int) or not (1 <= month_ordinal <= 12)
            ):
                errs.append(f"temporal annotation {ann_id!r} month_ordinal out of range [1,12]")
            kind = a.get("kind")
            if kind is not None and kind not in {"absolute", "relative"}:
                errs.append(f"temporal annotation {ann_id!r} kind={kind!r} must be 'absolute' or 'relative'")
            resolution = a.get("resolution")
            if resolution is not None and resolution not in ALLOWED_TEMPORAL_RESOLUTIONS:
                errs.append(
                    f"temporal annotation {ann_id!r} resolution={resolution!r} "
                    f"must be one of {sorted(ALLOWED_TEMPORAL_RESOLUTIONS)}"
                )
            if kind == "absolute" and "era" not in a:
                errs.append(f"absolute temporal annotation {ann_id!r} missing field: 'era'")
    return errs


def _classify_path(path: Path) -> str:
    """Decide which validator to run based on the path."""
    suf = path.suffix.lower()
    if suf == ".md":
        return "text"
    if suf in (".yaml", ".yml"):
        # Currently only annotations/ uses YAML; future variants/ files do too.
        if "annotations" in path.parts:
            return "annotation"
        if "variants" in path.parts:
            return "variant"  # placeholder; variant validator not yet implemented
    return "skip"


def _expand_root(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return list(walk_texts(root)) + list(walk_annotations(root))


def run(roots: Iterable[Path], *, repo_root: Path | None = None) -> tuple[int, int]:
    """Validate every recognized file under each root. Returns (n_files, n_failed)."""
    repo_root = repo_root or Path.cwd()
    n_files = 0
    n_failed = 0
    for root in roots:
        if not root.exists():
            print(f"WARN: path does not exist: {root}", file=sys.stderr)
            continue
        for f in _expand_root(root):
            kind = _classify_path(f)
            if kind == "text":
                errs = validate_text_file(f)
            elif kind == "annotation":
                errs = validate_annotation_file(f, repo_root=repo_root)
            elif kind == "skip":
                continue
            else:
                # Unimplemented validator (e.g. variants); skip without counting as failure.
                continue
            n_files += 1
            if errs:
                n_failed += 1
                print(f"FAIL {f}")
                for e in errs:
                    print(f"  - {e}")
            else:
                print(f"OK   {f}")
    return n_files, n_failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate texts/ and annotations/ files against doc/format.md.")
    p.add_argument(
        "paths", nargs="*", type=Path,
        default=[Path("texts"), Path("annotations")],
        help="files or directories to check (default: texts/ and annotations/)",
    )
    p.add_argument("--repo-root", type=Path, default=Path.cwd(),
                   help="repo root used to resolve cross-file references (default: cwd)")
    args = p.parse_args(argv)
    n_files, n_failed = run(args.paths, repo_root=args.repo_root)
    print(f"\n{n_files} file(s) checked, {n_failed} failed")
    return 1 if n_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
