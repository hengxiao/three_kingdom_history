"""Find absolute date references in texts/ and write them as temporal annotations.

For each chapter, scans every segment for `<era><year>年[<season>?<month>月]?` and
emits one annotation entry per match. Annotations are merged into the existing
`annotations/<work>/<book>/<NN>.yaml` (which already contains 裴注 of type `pei`):

  - existing entries of `type: temporal` are dropped (idempotent regeneration);
  - new temporal entries get IDs `<segment-id>.t<N>`, numbered within each
    segment by document order (independent from the `aN` series used by 裴注);
  - all annotations are re-sorted by (anchor, at) for readability.

This first pass handles only absolute date forms. Relative time references
(`是歲`, `明年`, bare `<year>年`, bare `<month>月`) require carrying the most
recent absolute anchor across segments and will be implemented in a follow-up.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import yaml

_ANCHOR_PARSE_RE = re.compile(r"^([a-z]+)\.(\d+)\.p(\d+)([a-z]*)$")


def _anchor_sort_key(anchor: str) -> tuple:
    """Natural sort: 'wei.1.p5' < 'wei.1.p10' (numeric on juan and para_no)."""
    m = _ANCHOR_PARSE_RE.match(anchor)
    if not m:
        return ("", 0, 0, "")
    return (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))

from tools.batch_fetch import (
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths,
    load_config,
)
from tools.dates_resolver import TimelineState, resolve_segment
from tools.extract_annotations import annotations_path
from tools.segment import parse_file


@dataclass
class DatesResult:
    chapter_id: str
    annotations_path: Path
    n_temporal: int


@dataclass
class DatesError:
    entry: dict
    message: str


def build_temporal_annotations(text_path: Path, *, book: str) -> list[dict]:
    """Return temporal-annotation dicts for every date reference in the chapter.

    Walks segments in document order, carrying a TimelineState so relative refs
    (bare year/month, 是歲, 明年, 去年) can be resolved using the most recent
    absolute anchor. Both absolute and relative refs are emitted; the `kind`
    and `resolution` fields distinguish them.
    """
    parsed = parse_file(text_path)
    out: list[dict] = []
    state = TimelineState()
    for seg in parsed.segments:
        matches, state = resolve_segment(seg.text, book=book, state=state)
        for i, d in enumerate(matches, start=1):
            entry: dict = {
                "id": f"{seg.id}.t{i}",
                "anchor": seg.id,
                "at": d.at,
                "length": d.length,
                "type": "temporal",
                "text": d.surface,
                "kind": d.kind,
                "resolution": d.resolution,
                "year_ad": d.year_ad,
            }
            if d.era is not None:
                entry["era"] = d.era.name
                entry["era_year"] = d.era_year
            if d.month_chinese is not None:
                entry["month_chinese"] = d.month_chinese
                entry["month_ordinal"] = d.month_ordinal
            if d.reasoning is not None:
                entry["reasoning"] = d.reasoning
            if d.confidence is not None:
                entry["confidence"] = d.confidence
            out.append(entry)
    return out


def merge_temporal_into_file(annotations_yaml: Path, temporals: list[dict]) -> int:
    """Replace any existing temporal entries with `temporals`, preserving 裴注 etc.

    Returns the number of temporal entries written.
    """
    if not annotations_yaml.exists():
        raise FileNotFoundError(
            f"{annotations_yaml} missing — run `tools.extract_annotations` first to create it"
        )
    doc = yaml.safe_load(annotations_yaml.read_text(encoding="utf-8")) or {}
    existing = doc.get("annotations") or []
    keep = [a for a in existing if a.get("type") != "temporal"]
    merged = keep + list(temporals)
    # Sort by (anchor, at) using natural ordering on juan/para_no; tiebreak puts 裴注 (a*) before temporal (t*).
    merged.sort(key=lambda a: (_anchor_sort_key(str(a.get("anchor", ""))),
                               int(a.get("at", 0)),
                               0 if a.get("type") != "temporal" else 1))
    doc["annotations"] = merged
    annotations_yaml.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return len(temporals)


def process_one(entry: dict, *, repo_root: Path) -> DatesResult:
    text_path, _ = derive_paths(entry, repo_root=repo_root)
    if not text_path.exists():
        raise FileNotFoundError(f"{text_path} missing — run `tools.batch_fetch` first")
    book = entry["book"]
    juan = int(entry["juan"])
    chapter_id = f"{book}.{juan}"
    out_path = annotations_path(entry, repo_root=repo_root)
    temporals = build_temporal_annotations(text_path, book=book)
    n = merge_temporal_into_file(out_path, temporals)
    return DatesResult(chapter_id=chapter_id, annotations_path=out_path, n_temporal=n)


def run(
    config: Iterable[dict],
    *,
    repo_root: Path = REPO_ROOT_DEFAULT,
    only: set[int] | None = None,
) -> Iterator[DatesResult | DatesError]:
    for entry in config:
        if only is not None and entry["ctext_juan"] not in only:
            continue
        try:
            yield process_one(entry, repo_root=repo_root)
        except Exception as e:  # noqa: BLE001
            yield DatesError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract absolute-time annotations into annotations/.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--only", default=None, help="comma-separated ctext_juan numbers")
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_err = 0
    n_total = 0
    for r in run(config, only=only):
        if isinstance(r, DatesError):
            n_err += 1
            print(f"FAIL ctext={r.entry['ctext_juan']} ({r.entry['book']}/{r.entry['juan']}): {r.message}",
                  file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_temporal
            print(f"OK   {r.chapter_id}: {r.n_temporal} absolute date(s) → {r.annotations_path}")
    print(f"\n{n_ok} chapters, {n_total} temporal annotations total, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
