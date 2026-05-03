"""Run the same temporal-annotation extractor as tools.extract_dates, but for 后汉书.

Reuses build_temporal_annotations + merge_temporal_into_file (work-agnostic) from
tools.extract_dates; the only thing that changes is path derivation and the
fixed `book="hhs"` argument.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from tools.batch_fetch_hhs import (
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths,
    load_config,
)
from tools.extract_annotations_hhs import annotations_path
from tools.extract_dates import build_temporal_annotations, merge_temporal_into_file


@dataclass
class DatesResult:
    chapter_id: str
    annotations_path: Path
    n_temporal: int


@dataclass
class DatesError:
    entry: dict
    message: str


def process_one(entry: dict, *, repo_root: Path) -> DatesResult:
    text_path, _ = derive_paths(entry, repo_root=repo_root)
    if not text_path.exists():
        raise FileNotFoundError(f"{text_path} missing — run `tools.batch_fetch_hhs` first")
    juan = int(entry["juan"])
    chapter_id = f"hhs.{juan}"
    out_path = annotations_path(entry, repo_root=repo_root)
    temporals = build_temporal_annotations(text_path, book="hhs")
    n = merge_temporal_into_file(out_path, temporals)
    return DatesResult(chapter_id=chapter_id, annotations_path=out_path, n_temporal=n)


def run(config: Iterable[dict], *, repo_root: Path = REPO_ROOT_DEFAULT,
        only: set[int] | None = None) -> Iterator[DatesResult | DatesError]:
    for entry in config:
        if only is not None and int(entry["juan"]) not in only:
            continue
        try:
            yield process_one(entry, repo_root=repo_root)
        except Exception as e:  # noqa: BLE001
            yield DatesError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract temporal annotations for 后汉书 chapters.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--only", default=None)
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_err = 0
    n_total = 0
    for r in run(config, only=only):
        if isinstance(r, DatesError):
            n_err += 1
            print(f"FAIL juan={r.entry['juan']}: {r.message}", file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_temporal
            print(f"OK   {r.chapter_id}: {r.n_temporal} temporal anns → {r.annotations_path}")
    print(f"\n{n_ok} chapters, {n_total} temporal annotations, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
