"""Compare ctext source against wikisource canonical, emit variants/<book>/<NN>.yaml.

For each chapter:
  1. Load (or fetch) the ctext page snapshot from sources/ctext/<…>.html.
     If the page is a TOC (no `<td class="ctext">`), skip — that chapter is split
     across captcha-protected sub-pages on ctext, so no per-segment text is
     reachable through scraping.
  2. Parse ctext segments via tools.fetch_ctext.
  3. Parse the canonical wikisource segments via the existing texts/ md file
     (segment IDs and canonical text).
  4. Align by per-segment normalized hash using difflib.SequenceMatcher; record
     diff ops for paired segments and which segments stayed unaligned.
  5. Write variants/sanguozhi/<book>/<NN>.yaml.

Idempotent: rerunning produces deterministic output for the same input HTML.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator

import yaml

from tools.batch_fetch import (
    BOOK_TITLES,
    CONFIG_PATH_DEFAULT,
    REPO_ROOT_DEFAULT,
    derive_paths as wikisource_derive_paths,
    load_config,
)
from tools.diff_sources import classify
from tools.fetch_ctext import fetch as ctext_fetch_default, parse_ctext_html
from tools.segment import canonical_hash, normalized_hash, parse_file


SKIP_REASON_TOC = "ctext page is a TOC — chapter split into captcha-protected sub-pages"
SKIP_REASON_NO_HTML = "no <td class=\"ctext\"> cells found (unrecognized layout)"


def ctext_source_path(entry: dict, *, repo_root: Path) -> Path:
    book = entry["book"]
    nn = f"{int(entry['juan']):02d}"
    return repo_root / "sources" / "ctext" / "sanguozhi" / book / f"{nn}.html"


def variants_path(entry: dict, *, repo_root: Path) -> Path:
    book = entry["book"]
    nn = f"{int(entry['juan']):02d}"
    return repo_root / "variants" / "sanguozhi" / book / f"{nn}.yaml"


@dataclass
class VariantResult:
    chapter_id: str
    variants_path: Path
    n_canonical: int
    n_source: int
    n_aligned: int
    n_diffs: int
    n_unaligned_canonical: int
    n_unaligned_source: int


@dataclass
class VariantSkip:
    entry: dict
    reason: str


@dataclass
class VariantError:
    entry: dict
    message: str


def _ensure_ctext_html(
    entry: dict,
    *,
    repo_root: Path,
    fetcher: Callable[[str], bytes],
    mode: str = "auto",
) -> tuple[bytes, str] | None:
    """Return (raw_bytes, sha256) or None if the chapter is not single-page on ctext.

    `mode`:
      - "auto"      → use cached snapshot if present, otherwise fetch (idempotent retries)
      - "no-fetch"  → never hit the network; missing snapshot returns None
      - "refetch"   → always fetch, overwriting any cached snapshot
    """
    src = ctext_source_path(entry, repo_root=repo_root)
    cached = src.exists()
    if mode == "no-fetch":
        if not cached:
            return None
        raw = src.read_bytes()
    elif mode == "auto" and cached:
        raw = src.read_bytes()
    else:
        url = f"https://ctext.org/sanguozhi/{int(entry['ctext_juan'])}"
        raw = fetcher(url)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(raw)
    if b'<td class="ctext">' not in raw:
        return None
    return raw, hashlib.sha256(raw).hexdigest()


def _align_normalized(
    canonical_norm_hashes: list[str],
    source_norm_hashes: list[str],
) -> dict[int, int]:
    """Align canonical and source segments by normalized hash via difflib.SequenceMatcher.

    Returns {canonical_index → source_index} for confidently paired segments.
    Unmatched canonical and source indices stay out of the map.
    """
    matcher = difflib.SequenceMatcher(
        a=canonical_norm_hashes, b=source_norm_hashes, autojunk=False,
    )
    pairs: dict[int, int] = {}
    # Equal opcodes give us the trustworthy matches (normalized hashes are identical).
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k, ci in enumerate(range(i1, i2)):
                pairs[ci] = j1 + k
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            # Equal-length replace block: pair them positionally so we can record
            # textual differences (otherwise their data is lost). Skip when sizes
            # diverge — that signals real segmentation drift, leave both unaligned.
            for k, ci in enumerate(range(i1, i2)):
                pairs[ci] = j1 + k
        # 'insert' / 'delete' / 'replace' with size mismatch → leave unaligned
    return pairs


def _build_yaml_doc(
    entry: dict,
    *,
    canonical_segments,
    source_paragraphs,
    source_sha256: str,
    source_url: str,
    source_retrieved: str,
    source_id: str = "ctext",
) -> tuple[dict, VariantResult]:
    book = entry["book"]
    juan = int(entry["juan"])
    chapter_id = f"{book}.{juan}"

    canon_norm_hashes = [normalized_hash(s.text) for s in canonical_segments]
    source_norm_hashes = [normalized_hash(p.main_text) for p in source_paragraphs]
    pair_map = _align_normalized(canon_norm_hashes, source_norm_hashes)

    unaligned_canonical_ids = [
        canonical_segments[i].id
        for i in range(len(canonical_segments)) if i not in pair_map
    ]
    matched_source_indices = set(pair_map.values())
    unaligned_source = [
        i + 1 for i in range(len(source_paragraphs)) if i not in matched_source_indices
    ]

    segments: dict[str, dict] = {}
    n_diffs = 0
    for ci, si in sorted(pair_map.items()):
        canon = canonical_segments[ci]
        source = source_paragraphs[si]
        cls = classify(canon.text, source.main_text)
        if cls["kind"] == "equal":
            continue
        n_diffs += 1
        diff_entry: dict = {
            "source": source_id,
            "source_para_no": source.para_no,
            "kind": cls["kind"],
            "equal_normalized": cls["equal_normalized"],
        }
        if cls["ops"]:
            diff_entry["ops"] = cls["ops"]
        segments[canon.id] = {
            "canonical_hash": canonical_hash(canon.text),
            "canonical_normalized_hash": normalized_hash(canon.text),
            "diffs": [diff_entry],
        }

    doc: dict = {
        "chapter": chapter_id,
        "canonical": "wikisource",
        "sources": {
            source_id: {
                "url": source_url,
                "retrieved": source_retrieved,
                "file_sha256": source_sha256,
                "n_paragraphs": len(source_paragraphs),
            },
        },
        "alignment": {
            "method": "normalized_hash_diff",
            "n_canonical_segments": len(canonical_segments),
            "n_aligned": len(pair_map),
            "unaligned_canonical": unaligned_canonical_ids,
            "unaligned_source": {source_id: unaligned_source},
        },
        "segments": segments,
    }

    return doc, VariantResult(
        chapter_id=chapter_id,
        variants_path=variants_path(entry, repo_root=Path(".")),  # filled by caller
        n_canonical=len(canonical_segments),
        n_source=len(source_paragraphs),
        n_aligned=len(pair_map),
        n_diffs=n_diffs,
        n_unaligned_canonical=len(unaligned_canonical_ids),
        n_unaligned_source=len(unaligned_source),
    )


def process_one(
    entry: dict,
    *,
    repo_root: Path,
    retrieved: str,
    fetcher: Callable[[str], bytes] = ctext_fetch_default,
    mode: str = "auto",
) -> VariantResult | VariantSkip:
    fetched = _ensure_ctext_html(entry, repo_root=repo_root, fetcher=fetcher, mode=mode)
    if fetched is None:
        return VariantSkip(entry=entry, reason=SKIP_REASON_TOC)
    raw, source_sha = fetched

    ctext_chapter = parse_ctext_html(raw.decode("utf-8", errors="replace"), int(entry["ctext_juan"]))
    source_paragraphs = [p for p in ctext_chapter.paragraphs if p.main_text]
    if not source_paragraphs:
        return VariantSkip(entry=entry, reason=SKIP_REASON_NO_HTML)

    text_path, _ = wikisource_derive_paths(entry, repo_root=repo_root)
    canonical = parse_file(text_path)

    url = f"https://ctext.org/sanguozhi/{int(entry['ctext_juan'])}"
    doc, result = _build_yaml_doc(
        entry,
        canonical_segments=list(canonical.segments),
        source_paragraphs=source_paragraphs,
        source_sha256=source_sha,
        source_url=url,
        source_retrieved=retrieved,
    )

    out_path = variants_path(entry, repo_root=repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    result.variants_path = out_path
    return result


def run(
    config: Iterable[dict],
    *,
    repo_root: Path = REPO_ROOT_DEFAULT,
    retrieved: str | None = None,
    fetcher: Callable[[str], bytes] = ctext_fetch_default,
    sleeper: Callable[[float], None] = time.sleep,
    sleep_seconds: float = 3.0,
    only: set[int] | None = None,
    mode: str = "auto",
) -> Iterator[VariantResult | VariantSkip | VariantError]:
    """`mode` is one of 'auto' | 'no-fetch' | 'refetch' (see _ensure_ctext_html)."""
    retrieved = retrieved or date.today().isoformat()
    first_network = True
    for entry in config:
        if only is not None and entry["ctext_juan"] not in only:
            continue
        # Throttle only when we'll actually hit the network.
        will_hit_network = mode == "refetch" or (
            mode == "auto" and not ctext_source_path(entry, repo_root=repo_root).exists()
        )
        if will_hit_network and not first_network:
            sleeper(sleep_seconds)
        if will_hit_network:
            first_network = False
        try:
            yield process_one(
                entry, repo_root=repo_root, retrieved=retrieved,
                fetcher=fetcher, mode=mode,
            )
        except Exception as e:  # noqa: BLE001
            yield VariantError(entry=entry, message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build variants/ files comparing ctext to wikisource canonical.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    p.add_argument("--sleep", type=float, default=3.0)
    p.add_argument("--only", default=None)
    p.add_argument("--no-fetch", action="store_true",
                   help="reuse existing sources/ctext/ snapshots, don't hit the network")
    p.add_argument("--refetch", action="store_true",
                   help="always fetch, overwriting cached snapshots")
    p.add_argument("--retrieved", default=None)
    args = p.parse_args(argv)

    if args.no_fetch and args.refetch:
        p.error("--no-fetch and --refetch are mutually exclusive")
    mode = "no-fetch" if args.no_fetch else ("refetch" if args.refetch else "auto")

    config = load_config(args.config)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    n_ok = n_skip = n_err = 0
    for r in run(config, retrieved=args.retrieved, sleep_seconds=args.sleep,
                 only=only, mode=mode):
        if isinstance(r, VariantError):
            n_err += 1
            print(f"FAIL ctext={r.entry['ctext_juan']} ({r.entry['book']}/{r.entry['juan']}): {r.message}",
                  file=sys.stderr)
        elif isinstance(r, VariantSkip):
            n_skip += 1
            print(f"SKIP ctext={r.entry['ctext_juan']} ({r.entry['book']}/{r.entry['juan']}): {r.reason}",
                  file=sys.stderr)
        else:
            n_ok += 1
            print(
                f"OK   {r.chapter_id}: aligned {r.n_aligned}/{r.n_canonical} canonical "
                f"({r.n_source} source segs); {r.n_diffs} diffs"
            )
    print(f"\n{n_ok} chapters with variants, {n_skip} skipped, {n_err} failed", file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
