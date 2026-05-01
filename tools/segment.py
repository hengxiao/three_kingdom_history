"""Parse texts/ Markdown files, compute segment IDs and hashes per doc/format.md §6."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import yaml

try:
    from opencc import OpenCC
except ImportError as e:
    raise RuntimeError("opencc-python-reimplemented is required") from e

_T2S = OpenCC("t2s")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_ANCHOR_RE = re.compile(r'^<a id="([^"]+)"></a>\s*$')


class FormatError(ValueError):
    """Raised when a file violates doc/format.md."""


@dataclass(frozen=True)
class Segment:
    id: str
    text: str  # canonical paragraph text after whitespace collapse (see _collapse_ws)


@dataclass(frozen=True)
class ParsedFile:
    frontmatter: dict
    segments: tuple[Segment, ...]
    body: str  # raw text after frontmatter, preserved for round-trip writes


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def canonical_hash(text: str) -> str:
    """§6.1 — sha256 of paragraph after removing all whitespace."""
    return hashlib.sha256(_strip_ws(text).encode("utf-8")).hexdigest()


def normalized_hash(text: str) -> str:
    """§6.2 — sha256 after removing whitespace, stripping P*-category punctuation, then t2s."""
    s = _strip_ws(text)
    s = "".join(c for c in s if not unicodedata.category(c).startswith("P"))
    s = _T2S.convert(s)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def file_segments_sha256(segments: Iterable[Segment]) -> str:
    """§6.3 — sha256 of per-segment canonical_hash joined by '\\n' in ID-sorted order."""
    ordered = sorted(segments, key=lambda s: s.id)
    joined = "\n".join(canonical_hash(s.text) for s in ordered)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def parse_text(content: str) -> ParsedFile:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        raise FormatError("missing YAML frontmatter (file must start with '---' block)")
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise FormatError("frontmatter is not a YAML mapping")
    body = content[m.end():]
    segments = tuple(_extract_segments(body))
    _check_unique_ids(segments)
    return ParsedFile(frontmatter=fm, segments=segments, body=body)


def parse_file(path: Path | str) -> ParsedFile:
    return parse_text(Path(path).read_text(encoding="utf-8"))


def _extract_segments(body: str) -> Iterator[Segment]:
    lines = body.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        m = _ANCHOR_RE.match(line)
        if not m:
            raise FormatError(
                f"unexpected content outside a segment at line {i + 1}: {line!r}"
            )
        seg_id = m.group(1)
        i += 1
        para_lines: list[str] = []
        while i < n:
            nxt = lines[i]
            if nxt.strip() == "":
                break
            if _ANCHOR_RE.match(nxt):
                break
            para_lines.append(nxt)
            i += 1
        if not para_lines:
            raise FormatError(f"segment {seg_id!r} has empty body")
        yield Segment(id=seg_id, text=_strip_ws("\n".join(para_lines)))


def _check_unique_ids(segments: tuple[Segment, ...]) -> None:
    seen: set[str] = set()
    for s in segments:
        if s.id in seen:
            raise FormatError(f"duplicate segment id: {s.id}")
        seen.add(s.id)


def render_with_frontmatter(frontmatter: dict, body: str) -> str:
    """Reassemble file from frontmatter + body (used by --update)."""
    fm_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
    return f"---\n{fm_text}---\n{body}"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Verify or update segment hashes (doc/format.md §6).")
    p.add_argument("path", type=Path, help="text Markdown file")
    p.add_argument("--update", action="store_true", help="write segments_sha256 back into frontmatter")
    p.add_argument("--json", action="store_true", help="print per-segment hash table as JSON")
    args = p.parse_args(argv)

    parsed = parse_file(args.path)
    fsh = file_segments_sha256(parsed.segments)

    if args.update:
        new_fm = dict(parsed.frontmatter)
        new_fm["segments_sha256"] = fsh
        Path(args.path).write_text(render_with_frontmatter(new_fm, parsed.body), encoding="utf-8")
        print(f"updated: {args.path} segments_sha256={fsh}")
        return 0

    if args.json:
        print(json.dumps({
            "segments_sha256": fsh,
            "segments": [
                {
                    "id": s.id,
                    "canonical_hash": canonical_hash(s.text),
                    "normalized_hash": normalized_hash(s.text),
                }
                for s in parsed.segments
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    expected = parsed.frontmatter.get("segments_sha256")
    if expected and expected != fsh:
        print(f"MISMATCH {args.path}: expected={expected} actual={fsh}", file=sys.stderr)
        return 1
    print(f"OK {args.path} segments={len(parsed.segments)} segments_sha256={fsh}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
