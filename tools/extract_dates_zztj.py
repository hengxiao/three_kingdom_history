"""Extract temporal annotations for 资治通鉴 chapters.

资治通鉴 is编年 chronicle: every 卷 is a tightly dated stream of events with many
absolute reign-era refs at the top of each yearly subsection. We reuse the same
TimelineState walk as the other works and write a fresh annotations YAML per
chapter from scratch (no other annotation kinds to merge with — this Wikisource
edition has no 胡三省注).
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

from tools.batch_fetch_zztj import (
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths,
    load_config,
)
from tools.extract_dates import build_temporal_annotations


@dataclass
class DatesResult:
    chapter_id: str
    annotations_path: Path
    n_temporal: int


@dataclass
class DatesError:
    entry: dict
    message: str


def annotations_path(entry: dict, *, repo_root: Path) -> Path:
    nnn = f"{int(entry['juan']):03d}"
    return repo_root / "annotations" / "zztj" / f"{nnn}.yaml"


def process_one(entry: dict, *, repo_root: Path, retrieved: str) -> DatesResult:
    text_path, url_paths = derive_paths(entry, repo_root=repo_root)
    if not text_path.exists():
        raise FileNotFoundError(f"{text_path} missing — run `tools.batch_fetch_zztj` first")
    juan = int(entry["juan"])
    chapter_id = f"zztj.{juan}"

    src = url_paths[0][1]
    sha = hashlib.sha256(src.read_bytes()).hexdigest() if src.exists() else "0" * 64

    temporals = build_temporal_annotations(text_path, book="zztj")
    out_path = annotations_path(entry, repo_root=repo_root)
    doc = {
        "chapter": chapter_id,
        "source": {
            "id": "wikisource",
            "url": url_paths[0][0],
            "retrieved": retrieved,
            "sha256": sha,
        },
        "annotations": temporals,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120),
                        encoding="utf-8")
    return DatesResult(chapter_id=chapter_id, annotations_path=out_path, n_temporal=len(temporals))


def run(config: Iterable[dict], *, repo_root: Path = REPO_ROOT_DEFAULT,
        retrieved: str | None = None,
        only: set[int] | None = None) -> Iterator[DatesResult | DatesError]:
    retrieved = retrieved or date.today().isoformat()
    for entry in config:
        if only is not None and int(entry["juan"]) not in only:
            continue
        try:
            yield process_one(entry, repo_root=repo_root, retrieved=retrieved)
        except Exception as e:  # noqa: BLE001
            yield DatesError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract temporal annotations for 资治通鉴 chapters.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--retrieved", default=None)
    p.add_argument("--only", default=None)
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_err = 0
    n_total = 0
    for r in run(config, only=only, retrieved=args.retrieved):
        if isinstance(r, DatesError):
            n_err += 1
            print(f"FAIL juan={r.entry['juan']}: {r.message}", file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_temporal
            print(f"OK   {r.chapter_id}: {r.n_temporal} temporal anns → {r.annotations_path}")
    print(f"\n{n_ok} chapters, {n_total} temporal anns total, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
