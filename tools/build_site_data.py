"""Convert texts/ + annotations/ into a static-site-friendly JSON tree under site/data/.

Output layout:

    site/data/
        index.json                              ← directory of every chapter, grouped by book
        sanguozhi/wei/01.json                   ← one file per 三國志 chapter
        sanguozhi/shu/01.json
        sanguozhi/wu/01.json
        houhanshu/hhs/08.json                   ← one file per 後漢書 chapter (灵帝+ scope)

Each `index.json` chapter entry carries its own `data_url` so the renderer just
fetches it directly without needing to know per-work conventions.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

from tools.batch_fetch import (
    BOOK_TITLES as SANGUOZHI_BOOK_TITLES,
    CONFIG_PATH_DEFAULT as SANGUOZHI_CONFIG,
    REPO_ROOT_DEFAULT,
    derive_paths as derive_paths_sanguozhi,
    load_config as load_config_sanguozhi,
)
from tools.batch_fetch_hhs import (
    BOOK as HHS_BOOK,
    BOOK_TITLE as HHS_BOOK_TITLE,
    CONFIG_PATH_DEFAULT as HHS_CONFIG,
    derive_paths as derive_paths_hhs,
    load_config as load_config_hhs,
)
from tools.batch_fetch_zztj import (
    BOOK as ZZTJ_BOOK,
    BOOK_TITLE as ZZTJ_BOOK_TITLE,
    CONFIG_PATH_DEFAULT as ZZTJ_CONFIG,
    derive_paths as derive_paths_zztj,
    load_config as load_config_zztj,
)
from tools.extract_annotations import annotations_path as annotations_path_sanguozhi
from tools.extract_annotations_hhs import annotations_path as annotations_path_hhs
from tools.extract_dates_zztj import annotations_path as annotations_path_zztj
from tools.segment import parse_file


@dataclass
class BuildResult:
    chapter_id: str
    out_path: Path
    n_segments: int
    n_pei: int
    n_temporal: int


@dataclass
class WorkSpec:
    work_id: str               # "sanguozhi" | "houhanshu"
    work_title: str            # 三國志 / 後漢書
    config_path: Path
    load_config: Callable[[Path], list[dict]]
    derive_paths: Callable[..., tuple[Path, object]]      # (text_path, ...)
    annotations_path: Callable[..., Path]
    book_titles: dict[str, str]                            # book_id → display title
    book_order: list[str]                                  # ordering hint for index
    book_for_entry: Callable[[dict], str] = field(default=lambda e: e["book"])


WORKS: list[WorkSpec] = [
    WorkSpec(
        work_id="sanguozhi",
        work_title="三國志",
        config_path=SANGUOZHI_CONFIG,
        load_config=load_config_sanguozhi,
        derive_paths=derive_paths_sanguozhi,
        annotations_path=annotations_path_sanguozhi,
        book_titles=SANGUOZHI_BOOK_TITLES,
        book_order=["wei", "shu", "wu"],
    ),
    WorkSpec(
        work_id="houhanshu",
        work_title="後漢書",
        config_path=HHS_CONFIG,
        load_config=load_config_hhs,
        derive_paths=derive_paths_hhs,
        annotations_path=annotations_path_hhs,
        book_titles={HHS_BOOK: HHS_BOOK_TITLE},
        book_order=[HHS_BOOK],
        book_for_entry=lambda e: HHS_BOOK,
    ),
    WorkSpec(
        work_id="zztj",
        work_title="資治通鑑",
        config_path=ZZTJ_CONFIG,
        load_config=load_config_zztj,
        derive_paths=derive_paths_zztj,
        annotations_path=annotations_path_zztj,
        book_titles={ZZTJ_BOOK: ZZTJ_BOOK_TITLE},
        book_order=[ZZTJ_BOOK],
        book_for_entry=lambda e: ZZTJ_BOOK,
    ),
]


def _site_data_dir(repo_root: Path) -> Path:
    return repo_root / "site" / "data"


def _chapter_data_path(work_id: str, book: str, juan: int, *, repo_root: Path) -> Path:
    return _site_data_dir(repo_root) / work_id / book / f"{juan:02d}.json"


def _chapter_data_url(work_id: str, book: str, juan: int) -> str:
    return f"data/{work_id}/{book}/{juan:02d}.json"


def _strip_annotation_for_site(a: dict) -> dict:
    out = {
        "id": a["id"],
        "type": a["type"],
        "at": a["at"],
        "length": a.get("length", 0),
        "text": a.get("text", ""),
    }
    if a["type"] == "temporal":
        out["kind"] = a.get("kind", "absolute")
        out["resolution"] = a.get("resolution", "absolute")
        out["year_ad"] = a.get("year_ad")
        if a.get("era") is not None:
            out["era"] = a["era"]
        if a.get("era_year") is not None:
            out["era_year"] = a["era_year"]
        if "month_chinese" in a:
            out["month_chinese"] = a["month_chinese"]
        if "month_ordinal" in a:
            out["month_ordinal"] = a["month_ordinal"]
    return out


def build_chapter_dict(entry: dict, *, work: WorkSpec, repo_root: Path) -> dict:
    """Build the JSON dict for one chapter, using the work-specific path resolvers."""
    text_path = work.derive_paths(entry, repo_root=repo_root)[0]
    parsed = parse_file(text_path)
    fm = parsed.frontmatter
    book = work.book_for_entry(entry)

    ann_path = work.annotations_path(entry, repo_root=repo_root)
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
        "id": f"{book}.{fm['juan']}",
        "work": work.work_id,
        "work_title": work.work_title,
        "book": book,
        "book_title": fm.get("book_title", work.book_titles.get(book, book)),
        "juan": fm["juan"],
        "title": fm["title"],
        "author": fm.get("author"),
        "category": fm.get("category"),
        "source": {
            "id": fm["source"]["id"],
            "url": fm["source"]["url"],
            "retrieved": fm["source"]["retrieved"],
        },
        "parse_warnings": ann_doc.get("parse_warnings", []),
        "n_segments": len(segments_out),
        "segments": segments_out,
    }


def build_all(*, repo_root: Path = REPO_ROOT_DEFAULT,
              works: list[WorkSpec] | None = None) -> list[BuildResult]:
    out_dir = _site_data_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[BuildResult] = []
    # books: book_id → {title, work_id, chapters: [...]}
    books: dict[str, dict] = {}

    for work in (works if works is not None else WORKS):
        config = work.load_config(work.config_path)
        for entry in config:
            text_path = work.derive_paths(entry, repo_root=repo_root)[0]
            if not text_path.exists():
                continue
            ch = build_chapter_dict(entry, work=work, repo_root=repo_root)
            book = ch["book"]
            juan = int(ch["juan"])
            out = _chapter_data_path(work.work_id, book, juan, repo_root=repo_root)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(ch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            n_pei = sum(1 for s in ch["segments"] for a in s["annotations"]
                        if a["type"] in ("pei", "lixian"))
            n_temporal = sum(1 for s in ch["segments"] for a in s["annotations"]
                             if a["type"] == "temporal")
            results.append(BuildResult(
                chapter_id=ch["id"], out_path=out,
                n_segments=ch["n_segments"], n_pei=n_pei, n_temporal=n_temporal,
            ))
            # Index entry
            if book not in books:
                books[book] = {
                    "id": book,
                    "title": work.book_titles.get(book, book),
                    "work_id": work.work_id,
                    "work_title": work.work_title,
                    "chapters": [],
                }
            books[book]["chapters"].append({
                "id": ch["id"],
                "juan": juan,
                "title": ch["title"],
                "category": ch.get("category"),
                "data_url": _chapter_data_url(work.work_id, book, juan),
                "n_segments": ch["n_segments"],
                "n_pei": n_pei,
                "n_temporal": n_temporal,
            })

    # Stable ordering: combine each work's book_order list.
    book_id_order = []
    for work in WORKS:
        for b in work.book_order:
            if b in books and b not in book_id_order:
                book_id_order.append(b)
    ordered_books = []
    for b in book_id_order:
        books[b]["chapters"].sort(key=lambda c: c["juan"])
        ordered_books.append(books[b])

    index = {
        "generated_by": "tools/build_site_data.py",
        "books": ordered_books,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build static-site JSON from texts/ + annotations/.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    args = p.parse_args(argv)
    results = build_all(repo_root=args.repo_root)
    print(f"wrote {len(results)} chapter JSON file(s) + index.json under {_site_data_dir(args.repo_root)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
