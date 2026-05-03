"""Batch-fetch 资治通鉴 卷 from zh.wikisource.org per tools/zztj_chapters.yaml.

URL pattern: /資治通鑑/卷NNN (3-digit zero-pad). All single-page (no 上/下 splits).
The Wikisource transcription used here carries only 司马光 canonical text — no
胡三省注 — so we don't run an annotations extractor for zztj. Temporal anchors
are added in a separate pass via tools/extract_dates_zztj.
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
    fetch as default_fetch,
    parse_wikisource_html,
    render_markdown,
)

CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "zztj_chapters.yaml"
REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
WORK = "zztj"
WORK_TITLE = "資治通鑑"
BOOK = "zztj"
BOOK_TITLE = "資治通鑑"


@dataclass
class FetchResult:
    entry: dict
    text_path: Path
    source_path: Path
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
    return data


def derive_paths(entry: dict, *, repo_root: Path) -> tuple[Path, list[tuple[str, Path]]]:
    juan = int(entry["juan"])
    nnn = f"{juan:03d}"
    text_path = repo_root / "texts" / "zztj" / f"{nnn}.md"
    src = repo_root / "sources" / "wikisource" / "zztj" / f"{nnn}.html"
    url = f"https://zh.wikisource.org/wiki/資治通鑑/卷{nnn}"
    return text_path, [(url, src)]


def _ensure(url: str, src: Path, *, fetcher: Callable[[str], bytes], mode: str) -> bytes:
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


def process_one(
    entry: dict,
    *,
    repo_root: Path,
    retrieved: str,
    fetcher: Callable[[str], bytes] = default_fetch,
    mode: str = "auto",
) -> FetchResult:
    text_path, url_paths = derive_paths(entry, repo_root=repo_root)
    url, src = url_paths[0]
    raw = _ensure(url, src, fetcher=fetcher, mode=mode)
    sha = hashlib.sha256(raw).hexdigest()
    chapter = parse_wikisource_html(raw.decode("utf-8", errors="replace"), work=WORK)
    title = entry.get("title") or chapter.title
    if not title:
        raise ValueError(f"no title resolved for juan={entry['juan']}")

    md = render_markdown(
        chapter,
        work=WORK,
        work_title=WORK_TITLE,
        book=BOOK,
        book_title=BOOK_TITLE,
        work_prefix=BOOK,
        juan=int(entry["juan"]),
        title=title,
        author=entry.get("author", "司馬光"),
        source_url=url,
        source_sha256=sha,
        source_retrieved=retrieved,
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(md, encoding="utf-8")
    return FetchResult(
        entry=entry, text_path=text_path, source_path=src,
        n_segments=len(chapter.paragraphs), warnings=list(chapter.parse_warnings),
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
    p = argparse.ArgumentParser(description="Batch-fetch 资治通鉴 chapters listed in tools/zztj_chapters.yaml.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--sleep", type=float, default=3.0)
    p.add_argument("--only", default=None)
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
    for r in run(config, sleep_seconds=args.sleep, only=only, mode=mode, retrieved=args.retrieved):
        if isinstance(r, FetchError):
            n_err += 1
            print(f"FAIL juan={r.entry['juan']}: {r.message}", file=sys.stderr)
        else:
            n_ok += 1
            warn = f" [WARN: {'; '.join(r.warnings)}]" if r.warnings else ""
            print(f"OK   zztj.{r.entry['juan']:>2} → {r.text_path} ({r.n_segments} segments){warn}")
    print(f"\n{n_ok} ok, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
