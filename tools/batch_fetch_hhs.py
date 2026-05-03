"""Batch-fetch 後漢書 chapters from zh.wikisource.org per tools/houhanshu_chapters.yaml.

Differs from the sanguozhi pipeline in three ways:
  1. URL pattern is `/後漢書/卷N` (no zero pad).
  2. Some chapters are paginated as 卷N上 / 卷N下 — the `parts` config field lists
     the suffixes to fetch (in order); their bodies are concatenated paragraph-by-
     paragraph into a single canonical chapter file.
  3. The work-aware title normalizer in fetch_wikisource handles 后汉书 header
     forms (帝紀/列傳/etc.).

Output mirrors sanguozhi:
    sources/wikisource/houhanshu/<NN>[-<part>].html  (one file per fetched URL)
    texts/houhanshu/<NN>.md                          (concatenated single chapter)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator

import yaml

from tools.fetch_wikisource import (
    WSChapter,
    WSParagraph,
    fetch as default_fetch,
    parse_wikisource_html,
    render_markdown,
)

CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "houhanshu_chapters.yaml"
REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
ALLOWED_CATEGORIES = {"benji", "liezhuan", "zhi"}
WORK = "houhanshu"
WORK_TITLE = "後漢書"
BOOK = "hhs"
BOOK_TITLE = "後漢書"


@dataclass
class FetchResult:
    entry: dict
    text_path: Path
    source_paths: list[Path]
    n_segments: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class FetchError:
    entry: dict
    message: str


def load_config(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"config must be a YAML list, got {type(data).__name__}")
    for entry in data:
        if "juan" not in entry:
            raise ValueError(f"config entry missing 'juan': {entry}")
        if "category" not in entry or entry["category"] not in ALLOWED_CATEGORIES:
            raise ValueError(f"config entry needs category in {sorted(ALLOWED_CATEGORIES)}: {entry}")
    return data


def derive_paths(entry: dict, *, repo_root: Path) -> tuple[Path, list[tuple[str, Path]]]:
    """Return (text_md_path, [(part_url, source_html_path), ...])."""
    juan = int(entry["juan"])
    nn = f"{juan:02d}"
    text_path = repo_root / "texts" / "houhanshu" / f"{nn}.md"
    parts = entry.get("parts") or []
    src_root = repo_root / "sources" / "wikisource" / "houhanshu"
    if not parts:
        url = f"https://zh.wikisource.org/wiki/後漢書/卷{juan}"
        source_path = src_root / f"{nn}.html"
        return text_path, [(url, source_path)]
    pairs = []
    for p in parts:
        url = f"https://zh.wikisource.org/wiki/後漢書/卷{juan}{p}"
        pairs.append((url, src_root / f"{nn}-{p}.html"))
    return text_path, pairs


def _ensure_part(url: str, src: Path, *, fetcher: Callable[[str], bytes], mode: str) -> bytes:
    cached = src.exists()
    if mode == "no-fetch":
        if not cached:
            raise FileNotFoundError(f"--no-fetch but {src} does not exist")
        return src.read_bytes()
    if mode == "auto" and cached:
        return src.read_bytes()
    raw = fetcher(url)
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(raw)
    return raw


def _concat_chapters(parts: list[WSChapter]) -> WSChapter:
    """Merge multiple parsed wikisource pages into one chapter, renumbering paragraphs."""
    merged_paras: list[WSParagraph] = []
    warnings: list[str] = []
    for c in parts:
        for p in c.paragraphs:
            merged_paras.append(WSParagraph(
                para_no=len(merged_paras) + 1,
                main_text=p.main_text,
                annotations=list(p.annotations),
            ))
        warnings.extend(c.parse_warnings)
    title = parts[0].title  # use the first part's title; trailing-ordinal already stripped
    return WSChapter(title=title, paragraphs=merged_paras, parse_warnings=warnings)


def process_one(
    entry: dict,
    *,
    repo_root: Path,
    retrieved: str,
    fetcher: Callable[[str], bytes] = default_fetch,
    mode: str = "auto",
) -> FetchResult:
    text_path, url_paths = derive_paths(entry, repo_root=repo_root)
    parsed_parts: list[WSChapter] = []
    source_shas: list[tuple[str, str]] = []
    for url, src in url_paths:
        raw = _ensure_part(url, src, fetcher=fetcher, mode=mode)
        sha = hashlib.sha256(raw).hexdigest()
        source_shas.append((url, sha))
        parsed_parts.append(parse_wikisource_html(raw.decode("utf-8", errors="replace"), work=WORK))

    chapter = _concat_chapters(parsed_parts)
    title = entry.get("title") or chapter.title
    if not title:
        raise ValueError(f"no title resolved for juan={entry['juan']}")

    # First part's URL + sha go into source.url / source.sha256 (canonical pointer).
    # Additional parts are recorded under source.parts to keep the provenance complete.
    primary_url, primary_sha = source_shas[0]
    md = render_markdown(
        chapter,
        work=WORK,
        work_title=WORK_TITLE,
        book=BOOK,
        book_title=BOOK_TITLE,
        work_prefix=BOOK,
        juan=int(entry["juan"]),
        title=title,
        author=entry.get("author", "范曄"),
        source_url=primary_url,
        source_sha256=primary_sha,
        source_retrieved=retrieved,
    )
    if len(source_shas) > 1:
        # Append the per-part provenance into the rendered frontmatter.
        from tools.segment import parse_text, render_with_frontmatter
        parsed = parse_text(md)
        fm = dict(parsed.frontmatter)
        fm["source"]["parts"] = [{"url": u, "sha256": s} for u, s in source_shas]
        md = render_with_frontmatter(fm, parsed.body)

    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(md, encoding="utf-8")
    return FetchResult(
        entry=entry, text_path=text_path,
        source_paths=[s for _, s in url_paths],
        n_segments=len(chapter.paragraphs),
        warnings=list(chapter.parse_warnings),
    )


def run(
    config: Iterable[dict],
    *,
    repo_root: Path = REPO_ROOT_DEFAULT,
    retrieved: str | None = None,
    fetcher: Callable[[str], bytes] = default_fetch,
    sleeper: Callable[[float], None] = time.sleep,
    sleep_seconds: float = 3.0,
    only: set[int] | None = None,
    mode: str = "auto",
) -> Iterator[FetchResult | FetchError]:
    retrieved = retrieved or date.today().isoformat()
    first_network = True
    for entry in config:
        if only is not None and int(entry["juan"]) not in only:
            continue
        # Throttle only when at least one part will hit the network.
        will_hit = mode == "refetch"
        if mode == "auto":
            _, urls = derive_paths(entry, repo_root=repo_root)
            will_hit = any(not src.exists() for _, src in urls)
        if will_hit and not first_network:
            sleeper(sleep_seconds)
        if will_hit:
            first_network = False
        try:
            yield process_one(entry, repo_root=repo_root, retrieved=retrieved,
                              fetcher=fetcher, mode=mode)
        except Exception as e:  # noqa: BLE001
            yield FetchError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch-fetch 后汉书 chapters listed in tools/houhanshu_chapters.yaml.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--sleep", type=float, default=3.0)
    p.add_argument("--only", default=None, help="comma-separated juan numbers to fetch")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--refetch", action="store_true")
    p.add_argument("--retrieved", default=None)
    args = p.parse_args(argv)
    if args.no_fetch and args.refetch:
        p.error("--no-fetch and --refetch are mutually exclusive")
    mode = "no-fetch" if args.no_fetch else ("refetch" if args.refetch else "auto")

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_err = 0
    for r in run(config, sleep_seconds=args.sleep, only=only,
                 mode=mode, retrieved=args.retrieved):
        if isinstance(r, FetchError):
            n_err += 1
            print(f"FAIL juan={r.entry['juan']}: {r.message}", file=sys.stderr)
        else:
            n_ok += 1
            warn = f" [WARN: {'; '.join(r.warnings)}]" if r.warnings else ""
            print(f"OK   hhs.{r.entry['juan']:>2} → {r.text_path} ({r.n_segments} segments){warn}")
    print(f"\n{n_ok} ok, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
