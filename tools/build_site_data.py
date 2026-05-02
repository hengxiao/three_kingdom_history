"""Convert texts/ + annotations/ into a static-site-friendly JSON tree under site/data/.

Layout produced:

    site/data/
        index.json                   ← chapter directory (3 books × juans)
        sanguozhi/wei/01.json        ← per-chapter: segments + annotations grouped by anchor
        sanguozhi/shu/...
        sanguozhi/wu/...

The browser fetches these directly — no server-side rendering, no build step on
the client. The JSON is built from the same sources of truth that texts/ and
annotations/ use, so the site never drifts from the canonical corpus.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from tools.batch_fetch import (
    BOOK_TITLES,
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths,
    load_config,
)
from tools.extract_annotations import annotations_path
from tools.segment import parse_file


@dataclass
class BuildResult:
    chapter_id: str
    out_path: Path
    n_segments: int
    n_pei: int
    n_temporal: int


def _site_data_dir(repo_root: Path) -> Path:
    return repo_root / "site" / "data"


def _chapter_json_path(entry: dict, *, repo_root: Path) -> Path:
    book = entry["book"]
    nn = f"{int(entry['juan']):02d}"
    return _site_data_dir(repo_root) / "sanguozhi" / book / f"{nn}.json"


def build_chapter_dict(entry: dict, *, repo_root: Path) -> dict:
    """Build the per-chapter JSON-serializable dict."""
    text_path, _ = derive_paths(entry, repo_root=repo_root)
    parsed = parse_file(text_path)
    fm = parsed.frontmatter

    ann_path = annotations_path(entry, repo_root=repo_root)
    ann_doc: dict = {}
    if ann_path.exists():
        ann_doc = yaml.safe_load(ann_path.read_text(encoding="utf-8")) or {}
    anns_by_anchor: dict[str, list[dict]] = {}
    for a in ann_doc.get("annotations", []) or []:
        anns_by_anchor.setdefault(a["anchor"], []).append(_strip_annotation_for_site(a))

    segments_out: list[dict] = []
    for s in parsed.segments:
        segments_out.append({
            "id": s.id,
            "text": s.text,
            "annotations": anns_by_anchor.get(s.id, []),
        })

    return {
        "id": f"{fm['book']}.{fm['juan']}",
        "book": fm["book"],
        "book_title": fm["book_title"],
        "juan": fm["juan"],
        "title": fm["title"],
        "author": fm.get("author"),
        "source": {
            "id": fm["source"]["id"],
            "url": fm["source"]["url"],
            "retrieved": fm["source"]["retrieved"],
        },
        "parse_warnings": ann_doc.get("parse_warnings", []),
        "n_segments": len(segments_out),
        "segments": segments_out,
    }


def _strip_annotation_for_site(a: dict) -> dict:
    """Copy of the annotation dict with only the fields the site needs.

    Skips internal accounting fields the renderer doesn't use.
    """
    out = {
        "id": a["id"],
        "type": a["type"],
        "at": a["at"],
        "length": a.get("length", 0),
        "text": a.get("text", ""),
    }
    if a["type"] == "temporal":
        out["era"] = a.get("era")
        out["era_year"] = a.get("era_year")
        out["year_ad"] = a.get("year_ad")
        if "month_chinese" in a:
            out["month_chinese"] = a["month_chinese"]
        if "month_ordinal" in a:
            out["month_ordinal"] = a["month_ordinal"]
    return out


def build_index_dict(config: Iterable[dict], *, repo_root: Path) -> dict:
    """Build the index.json: list of books, each with its chapters + counts."""
    books: dict[str, dict] = {}
    for entry in config:
        book = entry["book"]
        if book not in books:
            books[book] = {
                "id": book,
                "title": BOOK_TITLES.get(book, book),
                "chapters": [],
            }
        text_path, _ = derive_paths(entry, repo_root=repo_root)
        if not text_path.exists():
            continue
        parsed = parse_file(text_path)
        fm = parsed.frontmatter
        # Count annotation types by reading the YAML (light parse).
        ann_path = annotations_path(entry, repo_root=repo_root)
        n_pei = n_temporal = 0
        if ann_path.exists():
            ann_doc = yaml.safe_load(ann_path.read_text(encoding="utf-8")) or {}
            for a in ann_doc.get("annotations", []) or []:
                t = a.get("type")
                if t == "pei":
                    n_pei += 1
                elif t == "temporal":
                    n_temporal += 1
        books[book]["chapters"].append({
            "id": f"{book}.{fm['juan']}",
            "juan": fm["juan"],
            "title": fm["title"],
            "n_segments": len(parsed.segments),
            "n_pei": n_pei,
            "n_temporal": n_temporal,
        })
    # Stable order: books in (wei, shu, wu) order; chapters by juan.
    book_order = ["wei", "shu", "wu"]
    return {
        "work": "sanguozhi",
        "work_title": "三國志",
        "generated_by": "tools/build_site_data.py",
        "books": [
            {**books[b], "chapters": sorted(books[b]["chapters"], key=lambda c: c["juan"])}
            for b in book_order if b in books
        ],
    }


def build_all(*, repo_root: Path = REPO_ROOT_DEFAULT, config_path: Path = CONFIG_PATH_DEFAULT) -> list[BuildResult]:
    config = load_config(config_path)
    out_dir = _site_data_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[BuildResult] = []
    for entry in config:
        text_path, _ = derive_paths(entry, repo_root=repo_root)
        if not text_path.exists():
            continue
        ch = build_chapter_dict(entry, repo_root=repo_root)
        out = _chapter_json_path(entry, repo_root=repo_root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n_pei = sum(1 for s in ch["segments"] for a in s["annotations"] if a["type"] == "pei")
        n_temporal = sum(1 for s in ch["segments"] for a in s["annotations"] if a["type"] == "temporal")
        results.append(BuildResult(
            chapter_id=ch["id"], out_path=out,
            n_segments=ch["n_segments"], n_pei=n_pei, n_temporal=n_temporal,
        ))

    index = build_index_dict(config, repo_root=repo_root)
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build static-site JSON from texts/ + annotations/.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    args = p.parse_args(argv)
    results = build_all(repo_root=args.repo_root, config_path=args.config)
    print(f"wrote {len(results)} chapter JSON file(s) + index.json under {_site_data_dir(args.repo_root)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
