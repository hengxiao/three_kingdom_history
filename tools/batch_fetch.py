"""Batch-fetch sanguozhi juans from zh.wikisource.org per tools/sanguozhi_chapters.yaml.

For each entry, fetches HTML → sources/wikisource/sanguozhi/<book>/<NN>.html, parses
canonical 正文 (stripping 裴注), renders → texts/sanguozhi/<book>/<NN>.md.

Throttles requests with --sleep (default 3s) to be polite to Wikipedia/MediaWiki. Use
--no-fetch to skip the network and reuse existing source snapshots, and
--resume to skip juans whose text file already exists.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator

import yaml

from tools.fetch_wikisource import fetch as default_fetch, parse_wikisource_html, render_markdown

BOOK_TITLES = {"wei": "魏書", "shu": "蜀書", "wu": "吳書"}

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
CONFIG_PATH_DEFAULT = REPO_ROOT_DEFAULT / "tools" / "sanguozhi_chapters.yaml"


@dataclass
class FetchResult:
    entry: dict
    text_path: Path
    source_path: Path
    n_segments: int
    n_skipped: int  # paragraphs that were entirely 裴注 with no canonical content
    warnings: list[str] = None  # non-fatal parser issues; populated for problem chapters

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


@dataclass
class FetchError:
    entry: dict
    message: str


def load_config(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"config must be a YAML list, got {type(data).__name__}")
    for entry in data:
        for k in ("ctext_juan", "book", "juan"):
            if k not in entry:
                raise ValueError(f"config entry missing required key {k!r}: {entry}")
        if entry["book"] not in BOOK_TITLES:
            raise ValueError(
                f"unknown book {entry['book']!r}; add to BOOK_TITLES in tools/batch_fetch.py"
            )
    return data


def derive_paths(entry: dict, *, repo_root: Path) -> tuple[Path, Path]:
    book = entry["book"]
    nn = f"{int(entry['juan']):02d}"
    text_path = repo_root / "texts" / "sanguozhi" / book / f"{nn}.md"
    source_path = repo_root / "sources" / "wikisource" / "sanguozhi" / book / f"{nn}.html"
    return text_path, source_path


def _process_one(
    entry: dict,
    *,
    fetcher: Callable[[str], bytes],
    repo_root: Path,
    retrieved: str,
    no_fetch: bool,
) -> FetchResult:
    text_path, source_path = derive_paths(entry, repo_root=repo_root)
    juan_global = int(entry["ctext_juan"])  # config key kept; same as wikisource global juan number
    url = f"https://zh.wikisource.org/wiki/三國志/卷{juan_global:02d}"
    if no_fetch:
        if not source_path.exists():
            raise FileNotFoundError(f"--no-fetch but {source_path} does not exist")
        raw = source_path.read_bytes()
    else:
        raw = fetcher(url)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    chapter = parse_wikisource_html(raw.decode("utf-8", errors="replace"))
    title = entry.get("title") or chapter.title
    if not title:
        raise ValueError(f"no title found on page or in config for global juan={juan_global}")

    md = render_markdown(
        chapter,
        work="sanguozhi",
        work_title="三國志",
        book=entry["book"],
        book_title=BOOK_TITLES[entry["book"]],
        work_prefix=entry["book"],
        juan=int(entry["juan"]),
        title=title,
        author=entry.get("author", "陳壽"),
        source_url=url,
        source_sha256=sha,
        source_retrieved=retrieved,
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(md, encoding="utf-8")
    return FetchResult(
        entry=entry, text_path=text_path, source_path=source_path,
        n_segments=len(chapter.paragraphs), n_skipped=0,
        warnings=list(chapter.parse_warnings),
    )


def batch(
    config: Iterable[dict],
    *,
    fetcher: Callable[[str], bytes] = default_fetch,
    repo_root: Path = REPO_ROOT_DEFAULT,
    sleep_seconds: float = 3.0,
    only: set[int] | None = None,
    no_fetch: bool = False,
    resume: bool = False,
    retrieved: str | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> Iterator[FetchResult | FetchError]:
    """Yield one FetchResult or FetchError per processed entry, in config order."""
    retrieved = retrieved or date.today().isoformat()
    first_network_call = True
    for entry in config:
        if only is not None and entry["ctext_juan"] not in only:
            continue
        text_path, _ = derive_paths(entry, repo_root=repo_root)
        if resume and text_path.exists():
            continue
        if not no_fetch and not first_network_call:
            sleeper(sleep_seconds)
        first_network_call = False
        try:
            result = _process_one(
                entry, fetcher=fetcher, repo_root=repo_root,
                retrieved=retrieved, no_fetch=no_fetch,
            )
            yield result
        except Exception as e:  # noqa: BLE001 — surface every error to caller
            yield FetchError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch-fetch sanguozhi juans from ctext.org.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--sleep", type=float, default=3.0, help="seconds between network requests")
    p.add_argument("--only", default=None,
                   help="comma-separated ctext_juan numbers to fetch (default: all)")
    p.add_argument("--no-fetch", action="store_true",
                   help="reuse existing sources/ snapshots instead of fetching")
    p.add_argument("--resume", action="store_true",
                   help="skip juans whose texts/<book>/<NN>.md already exists")
    p.add_argument("--retrieved", default=None, help="ISO date; defaults to today")
    args = p.parse_args(argv)

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = 0
    n_err = 0
    for r in batch(
        config,
        sleep_seconds=args.sleep,
        only=only,
        no_fetch=args.no_fetch,
        resume=args.resume,
        retrieved=args.retrieved,
    ):
        if isinstance(r, FetchError):
            n_err += 1
            print(
                f"FAIL ctext={r.entry['ctext_juan']} ({r.entry['book']}/{r.entry['juan']}): "
                f"{r.message}",
                file=sys.stderr,
            )
        else:
            n_ok += 1
            warn_tail = f" [WARN: {'; '.join(r.warnings)}]" if r.warnings else ""
            print(
                f"OK   ctext={r.entry['ctext_juan']} → {r.text_path} "
                f"({r.n_segments} segments){warn_tail}"
            )
    print(f"\n{n_ok} ok, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
