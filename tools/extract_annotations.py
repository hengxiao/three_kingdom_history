"""Re-parse sources/wikisource/<…>.html, write annotations/<…>.yaml per format §5.

Idempotent: produces deterministic output for the same input HTML, so re-running
should be a no-op in git unless the source changed. Reads the chapter config
(tools/sanguozhi_chapters.yaml) to know which juans to process.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from tools.batch_fetch import (
    BOOK_TITLES,
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths,
    load_config,
)
from tools.fetch_wikisource import WSChapter, parse_wikisource_html


@dataclass
class AnnotationsResult:
    chapter_id: str
    annotations_path: Path
    n_annotations: int
    parse_warnings: list[str]


@dataclass
class AnnotationsError:
    entry: dict
    message: str


def annotations_path(entry: dict, *, repo_root: Path) -> Path:
    book = entry["book"]
    nn = f"{int(entry['juan']):02d}"
    return repo_root / "annotations" / "sanguozhi" / book / f"{nn}.yaml"


def render_annotations_yaml(
    chapter: WSChapter,
    *,
    chapter_id: str,
    work_prefix: str,
    juan: int,
    source_url: str,
    source_sha256: str,
    source_retrieved: str,
) -> str:
    """Build the YAML body of an annotations/ file from a parsed chapter."""
    items: list[dict] = []
    for p in chapter.paragraphs:
        seg_id = f"{work_prefix}.{juan}.p{p.para_no}"
        # Within a paragraph, annotations are produced in document order; their `at`
        # is monotonically non-decreasing. The `aN` index reflects that order.
        for j, ann in enumerate(p.annotations, start=1):
            items.append({
                "id": f"{seg_id}.a{j}",
                "anchor": seg_id,
                "at": ann.at,
                "length": 0,
                "type": "pei",
                "text": ann.text,
            })
    doc: dict = {
        "chapter": chapter_id,
        "source": {
            "id": "wikisource",
            "url": source_url,
            "retrieved": source_retrieved,
            "sha256": source_sha256,
        },
    }
    if chapter.parse_warnings:
        doc["parse_warnings"] = list(chapter.parse_warnings)
    doc["annotations"] = items
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)


def process_one(
    entry: dict,
    *,
    repo_root: Path,
    retrieved: str,
) -> AnnotationsResult:
    _, source_path = derive_paths(entry, repo_root=repo_root)
    if not source_path.exists():
        raise FileNotFoundError(
            f"{source_path} missing — run `tools.batch_fetch` to populate sources/ first"
        )
    raw = source_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    chapter = parse_wikisource_html(raw.decode("utf-8", errors="replace"))

    book = entry["book"]
    juan = int(entry["juan"])
    chapter_id = f"{book}.{juan}"
    juan_global = int(entry["ctext_juan"])
    url = f"https://zh.wikisource.org/wiki/三國志/卷{juan_global:02d}"

    out_path = annotations_path(entry, repo_root=repo_root)
    yaml_text = render_annotations_yaml(
        chapter,
        chapter_id=chapter_id,
        work_prefix=book,
        juan=juan,
        source_url=url,
        source_sha256=sha,
        source_retrieved=retrieved,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text, encoding="utf-8")
    return AnnotationsResult(
        chapter_id=chapter_id,
        annotations_path=out_path,
        n_annotations=sum(len(p.annotations) for p in chapter.paragraphs),
        parse_warnings=list(chapter.parse_warnings),
    )


def run(
    config: Iterable[dict],
    *,
    repo_root: Path = REPO_ROOT_DEFAULT,
    only: set[int] | None = None,
    retrieved: str | None = None,
) -> Iterator[AnnotationsResult | AnnotationsError]:
    retrieved = retrieved or date.today().isoformat()
    for entry in config:
        if only is not None and entry["ctext_juan"] not in only:
            continue
        try:
            yield process_one(entry, repo_root=repo_root, retrieved=retrieved)
        except Exception as e:  # noqa: BLE001
            yield AnnotationsError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract 裴注 annotations from sources/wikisource into annotations/.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--retrieved", default=None, help="ISO date; defaults to today")
    p.add_argument("--only", default=None, help="comma-separated ctext_juan numbers to process")
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = 0
    n_err = 0
    n_total = 0
    for r in run(config, only=only, retrieved=args.retrieved):
        if isinstance(r, AnnotationsError):
            n_err += 1
            print(f"FAIL ctext={r.entry['ctext_juan']} ({r.entry['book']}/{r.entry['juan']}): {r.message}",
                  file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_annotations
            warn = f" [WARN: {len(r.parse_warnings)}]" if r.parse_warnings else ""
            print(f"OK   {r.chapter_id}: {r.n_annotations} annotations → {r.annotations_path}{warn}")
    print(f"\n{n_ok} chapters, {n_total} annotations total, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
