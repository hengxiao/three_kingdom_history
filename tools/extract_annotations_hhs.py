"""Re-parse 後漢書 sources/wikisource pages, write annotations/houhanshu/<NN>.yaml.

Companion to tools/extract_annotations.py — same shape, but for 后汉书 chapters
(`type: lixian` for 李賢注). Multi-part chapters (上/下 splits) are concatenated
paragraph-by-paragraph to mirror what batch_fetch_hhs writes into texts/.
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

from tools.batch_fetch_hhs import (
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    _concat_chapters,
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
    nn = f"{int(entry['juan']):02d}"
    return repo_root / "annotations" / "houhanshu" / f"{nn}.yaml"


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
    items: list[dict] = []
    for p in chapter.paragraphs:
        seg_id = f"{work_prefix}.{juan}.p{p.para_no}"
        for j, ann in enumerate(p.annotations, start=1):
            items.append({
                "id": f"{seg_id}.a{j}",
                "anchor": seg_id,
                "at": ann.at,
                "length": 0,
                "type": "lixian",
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


def process_one(entry: dict, *, repo_root: Path, retrieved: str) -> AnnotationsResult:
    _text_path, url_paths = derive_paths(entry, repo_root=repo_root)
    parsed_parts: list[WSChapter] = []
    primary_url, primary_sha = None, None
    for url, src in url_paths:
        if not src.exists():
            raise FileNotFoundError(
                f"{src} missing — run `tools.batch_fetch_hhs` to populate sources/ first"
            )
        raw = src.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if primary_url is None:
            primary_url, primary_sha = url, sha
        parsed_parts.append(parse_wikisource_html(raw.decode("utf-8", errors="replace"),
                                                   work="houhanshu"))
    chapter = _concat_chapters(parsed_parts)

    juan = int(entry["juan"])
    chapter_id = f"hhs.{juan}"
    out_path = annotations_path(entry, repo_root=repo_root)
    yaml_text = render_annotations_yaml(
        chapter,
        chapter_id=chapter_id,
        work_prefix="hhs",
        juan=juan,
        source_url=primary_url,
        source_sha256=primary_sha,
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


def run(config: Iterable[dict], *, repo_root: Path = REPO_ROOT_DEFAULT,
        only: set[int] | None = None,
        retrieved: str | None = None) -> Iterator[AnnotationsResult | AnnotationsError]:
    retrieved = retrieved or date.today().isoformat()
    for entry in config:
        if only is not None and int(entry["juan"]) not in only:
            continue
        try:
            yield process_one(entry, repo_root=repo_root, retrieved=retrieved)
        except Exception as e:  # noqa: BLE001
            yield AnnotationsError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract 李賢注 annotations from 後漢書 sources/wikisource pages.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--retrieved", default=None)
    p.add_argument("--only", default=None, help="comma-separated juan numbers to process")
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_err = 0
    n_total = 0
    for r in run(config, only=only, retrieved=args.retrieved):
        if isinstance(r, AnnotationsError):
            n_err += 1
            print(f"FAIL juan={r.entry['juan']}: {r.message}", file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_annotations
            warn = f" [WARN: {len(r.parse_warnings)}]" if r.parse_warnings else ""
            print(f"OK   {r.chapter_id}: {r.n_annotations} annotations → {r.annotations_path}{warn}")
    print(f"\n{n_ok} chapters, {n_total} annotations total, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
