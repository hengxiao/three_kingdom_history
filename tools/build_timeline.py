"""Cross-source timeline: group every temporal annotation by AD year.

Walks annotations/<…>.yaml across both 三国志 and 后汉书, collects every
`type: temporal` reference, and emits site/data/timeline.json with one
entry per AD year. Each event carries enough context (chapter title,
data URL, segment id, snippet, kind/resolution) for the website to
render a year-by-year reading view that aligns the two sources.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from tools.segment import parse_file

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]


@dataclass
class TemporalRef:
    year_ad: int
    chapter_id: str            # e.g. "wei.1" or "hhs.8"
    chapter_title: str          # e.g. "武帝紀"
    book_title: str             # e.g. "魏書" or "後漢書"
    work: str                  # "sanguozhi" | "houhanshu"
    book: str
    juan: int
    anchor: str                # segment id, e.g. "wei.1.p5"
    at: int
    kind: str                  # absolute | relative
    resolution: str
    surface: str               # the text matched in the source (e.g. "中平六年")
    era: str | None
    era_year: int | None
    month_chinese: str | None
    month_ordinal: int | None
    snippet: str               # short string of segment text around the anchor
    reasoning: str | None      # short prose: "why is this year_ad?" — propagated to UI


def _chapter_data_url(work: str, book: str, juan: int) -> str:
    return f"data/{work}/{book}/{juan:02d}.json"


def _text_path_for(work: str, book: str, juan: int, *, repo_root: Path) -> Path:
    if work == "houhanshu":
        return repo_root / "texts" / "houhanshu" / f"{juan:02d}.md"
    if work == "zztj":
        return repo_root / "texts" / "zztj" / f"{juan:03d}.md"
    return repo_root / "texts" / "sanguozhi" / book / f"{juan:02d}.md"


def _annotation_files(repo_root: Path) -> Iterable[tuple[str, str, int, Path]]:
    """Yield (work, book, juan, ann_yaml_path) for every per-chapter annotations file."""
    sg_root = repo_root / "annotations" / "sanguozhi"
    if sg_root.exists():
        for book_dir in sorted(sg_root.iterdir()):
            if not book_dir.is_dir():
                continue
            for f in sorted(book_dir.glob("*.yaml")):
                juan = int(f.stem)
                yield ("sanguozhi", book_dir.name, juan, f)
    hhs_root = repo_root / "annotations" / "houhanshu"
    if hhs_root.exists():
        for f in sorted(hhs_root.glob("*.yaml")):
            juan = int(f.stem)
            yield ("houhanshu", "hhs", juan, f)
    zztj_root = repo_root / "annotations" / "zztj"
    if zztj_root.exists():
        for f in sorted(zztj_root.glob("*.yaml")):
            juan = int(f.stem)
            yield ("zztj", "zztj", juan, f)


def _make_snippet(seg_text: str, at: int, *, before: int = 25, after: int = 65) -> str:
    """Return a small slice of the segment around the anchor position, with elision marks."""
    start = max(0, at - before)
    end = min(len(seg_text), at + after)
    snippet = seg_text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(seg_text):
        snippet = snippet + "…"
    return snippet


def collect_refs(repo_root: Path = REPO_ROOT_DEFAULT) -> list[TemporalRef]:
    refs: list[TemporalRef] = []
    for work, book, juan, ann_path in _annotation_files(repo_root):
        text_path = _text_path_for(work, book, juan, repo_root=repo_root)
        if not text_path.exists():
            continue
        parsed = parse_file(text_path)
        seg_text_by_id = {s.id: s.text for s in parsed.segments}
        chapter_title = parsed.frontmatter.get("title", "")
        book_title = parsed.frontmatter.get("book_title", book)

        ann_doc = yaml.safe_load(ann_path.read_text(encoding="utf-8")) or {}
        for a in ann_doc.get("annotations", []) or []:
            if a.get("type") != "temporal":
                continue
            year = a.get("year_ad")
            if not isinstance(year, int):
                continue
            anchor = a.get("anchor")
            seg_text = seg_text_by_id.get(anchor, "")
            at = int(a.get("at", 0))
            refs.append(TemporalRef(
                year_ad=year,
                chapter_id=f"{book}.{juan}",
                chapter_title=chapter_title,
                book_title=book_title,
                work=work,
                book=book,
                juan=juan,
                anchor=anchor,
                at=at,
                kind=a.get("kind", "absolute"),
                resolution=a.get("resolution", "absolute"),
                surface=a.get("text", ""),
                era=a.get("era"),
                era_year=a.get("era_year"),
                month_chinese=a.get("month_chinese"),
                month_ordinal=a.get("month_ordinal"),
                snippet=_make_snippet(seg_text, at) if seg_text else "",
                reasoning=a.get("reasoning"),
            ))
    return refs


def build_timeline_dict(refs: list[TemporalRef]) -> dict:
    """Group refs by year_ad and emit the final timeline structure."""
    by_year: dict[int, list[TemporalRef]] = {}
    for r in refs:
        by_year.setdefault(r.year_ad, []).append(r)

    years = []
    for y in sorted(by_year):
        events = by_year[y]
        # Distinct era labels for this year (for header display).
        seen = set()
        labels = []
        for e in events:
            if e.era is None or e.era_year is None:
                continue
            key = (e.era, e.era_year)
            if key in seen:
                continue
            seen.add(key)
            year_str = "元" if e.era_year == 1 else _int_to_chinese(e.era_year)
            labels.append({
                "era": e.era,
                "era_year": e.era_year,
                "label": f"{e.era}{year_str}年",
            })
        labels.sort(key=lambda lab: (lab["era"], lab["era_year"]))

        # Sort events: absolute before relative (more reliable signal first), then
        # by chapter, then by anchor position.
        events.sort(key=lambda e: (
            0 if e.kind == "absolute" else 1,
            e.work, e.book, e.juan, e.anchor, e.at,
        ))
        years.append({
            "year_ad": y,
            "labels": labels,
            "n_events": len(events),
            "events": [_event_dict(e) for e in events],
        })
    return {
        "generated_by": "tools/build_timeline.py",
        "years": years,
    }


_CHINESE_DIGITS_REVERSE = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
                          6: "六", 7: "七", 8: "八", 9: "九"}


def _int_to_chinese(n: int) -> str:
    """Convert 1..99 to 一..九十九 (rough; era_year rarely exceeds 30)."""
    if 1 <= n <= 9:
        return _CHINESE_DIGITS_REVERSE[n]
    if n == 10:
        return "十"
    if 11 <= n <= 19:
        return "十" + _CHINESE_DIGITS_REVERSE[n - 10]
    if 20 <= n <= 99:
        tens = n // 10
        units = n % 10
        head = _CHINESE_DIGITS_REVERSE[tens] + "十"
        return head + (_CHINESE_DIGITS_REVERSE[units] if units else "")
    return str(n)


def _event_dict(e: TemporalRef) -> dict:
    out = {
        "chapter_id": e.chapter_id,
        "chapter_title": e.chapter_title,
        "book_title": e.book_title,
        "work": e.work,
        "anchor": e.anchor,
        "at": e.at,
        "data_url": _chapter_data_url(e.work, e.book, e.juan),
        "kind": e.kind,
        "resolution": e.resolution,
        "surface": e.surface,
        "era": e.era,
        "era_year": e.era_year,
        "month_chinese": e.month_chinese,
        "month_ordinal": e.month_ordinal,
        "snippet": e.snippet,
    }
    if e.reasoning:
        out["reasoning"] = e.reasoning
    return out


def write_timeline(repo_root: Path = REPO_ROOT_DEFAULT) -> Path:
    refs = collect_refs(repo_root=repo_root)
    timeline = build_timeline_dict(refs)
    out_path = repo_root / "site" / "data" / "timeline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build site/data/timeline.json from temporal annotations.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    args = p.parse_args(argv)
    out = write_timeline(repo_root=args.repo_root)
    refs = collect_refs(repo_root=args.repo_root)
    print(f"wrote {out} — {len(refs)} temporal refs across "
          f"{len({r.year_ad for r in refs})} years",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
