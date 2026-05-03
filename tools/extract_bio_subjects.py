"""Scan 列傳-style chapter text for `X字Y` introductions and emit candidate
person entries to merge into `tools/people.yaml`.

Heuristic: a 2-3 char personal name followed by `字` and a 2-char courtesy
name, flanked by sentence punctuation, is the canonical bio-subject opening
in 三國志/後漢書. Examples:

    「夏侯惇字元讓，沛國譙人也，夏侯嬰之後也。」
    「程昱字仲德，東郡東阿人也。」
    「荀彧字文若，潁川潁陰人也。」

Filters applied:
  - Name must appear ≥ MIN_REPEAT_IN_CHAPTER times in the chapter (avoids
    one-off references).
  - Skip candidates whose `primary_name` already appears in people.yaml
    (by primary_name OR by alias).
  - Skip well-known false positives via NAME_BLACKLIST (e.g., 列女, 良吏 —
    section headers that fit the pattern shape but aren't persons).

Output is a YAML snippet on stdout that can be reviewed and pasted into
`tools/people.yaml`. We deliberately do NOT auto-merge — bio chapter
attribution and quality control benefit from human review.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml
from opencc import OpenCC
from pypinyin import lazy_pinyin

from tools.batch_fetch import (
    CONFIG_PATH_DEFAULT as SANGUOZHI_CONFIG,
    REPO_ROOT_DEFAULT,
    derive_paths as derive_paths_sanguozhi,
    load_config as load_config_sanguozhi,
)
from tools.batch_fetch_hhs import (
    CONFIG_PATH_DEFAULT as HHS_CONFIG,
    derive_paths as derive_paths_hhs,
    load_config as load_config_hhs,
)
from tools.batch_fetch_zztj import (
    CONFIG_PATH_DEFAULT as ZZTJ_CONFIG,
    derive_paths as derive_paths_zztj,
    load_config as load_config_zztj,
)
from tools.segment import parse_file

PEOPLE_YAML = Path(__file__).resolve().parent / "people.yaml"

_S2T = OpenCC("s2t")


def _is_canonical_traditional(s: str) -> bool:
    """Skip candidates whose name contains simplified-only characters.

    Some 三國志 chapters have segments mixing simplified and traditional
    (a known data-quality issue from the Wikisource fetch). Those candidates
    won't be findable in canonically-traditional text, so we drop them here.
    """
    return _S2T.convert(s) == s

# Bio-opening: name (2-3 CJK chars) + 字 + courtesy (2 CJK chars).
# Lookbehind/ahead constrain to sentence boundaries.
_SENTENCE_BOUNDS = "。，、；：「」『』"
_BIO_RE = re.compile(
    rf"(?:(?<=[{_SENTENCE_BOUNDS}])|^|(?<=\n))"
    r"([一-鿿]{2,3})字([一-鿿]{2})"
    rf"(?=[{_SENTENCE_BOUNDS}])"
)

# Section/category headers shaped like X字Y but obviously not persons.
NAME_BLACKLIST = {
    # Common 卷篇名 fragments
    "列女", "良吏", "酷吏", "宦者", "獨行", "方術", "逸民", "文苑",
    "儒林", "黨錮", "西羌", "南蠻", "東夷", "西域", "鮮卑", "烏丸",
    # Reflexive/pronominal contexts
    "其後", "其子", "之子", "之孫", "其孫", "之父",
    # Wei.20 introduces sons by 封號 — prefer 曹X form (already in roster)
    "楚王彪", "燕王宇",
    # 異體字 already covered by aliases on existing entries
    "陳羣",
}

# Min number of times the candidate name must appear in the chapter before
# we believe it's a real person rather than a one-off textual coincidence.
# 3 surfaces only chapter principals; 2 also picks up 附傳 sub-subjects (e.g.,
# 諸葛瞻 in 諸葛亮傳) and persons introduced mid-narrative in zztj.
MIN_REPEAT_IN_CHAPTER = 2


@dataclass
class Candidate:
    primary_name: str
    courtesy_name: str
    chapter_ids: list[str]
    n_occurrences: int  # total occurrences across all chapters

    @property
    def best_bio_chapter(self) -> str:
        # Prefer the FIRST chapter in document order — bio chapters typically
        # introduce the subject early in the chapter sequence.
        return self.chapter_ids[0]


def _existing_names(people_yaml: Path) -> set[str]:
    if not people_yaml.exists():
        return set()
    data = yaml.safe_load(people_yaml.read_text(encoding="utf-8")) or []
    out: set[str] = set()
    for p in data:
        out.add(p["primary_name"])
        for a in (p.get("aliases") or []):
            out.add(a)
    return out


def _walk_chapters(repo_root: Path):
    """Yield (chapter_id, full_text) for each sanguozhi + houhanshu chapter."""
    sg_cfg = load_config_sanguozhi(SANGUOZHI_CONFIG)
    for entry in sg_cfg:
        path = derive_paths_sanguozhi(entry, repo_root=repo_root)[0]
        if not path.exists():
            continue
        parsed = parse_file(path)
        ch_id = f"{entry['book']}.{entry['juan']}"
        full_text = "\n".join(s.text for s in parsed.segments)
        yield ch_id, full_text

    hhs_cfg = load_config_hhs(HHS_CONFIG)
    for entry in hhs_cfg:
        path = derive_paths_hhs(entry, repo_root=repo_root)[0]
        if not path.exists():
            continue
        parsed = parse_file(path)
        ch_id = f"hhs.{entry['juan']}"
        full_text = "\n".join(s.text for s in parsed.segments)
        yield ch_id, full_text

    zztj_cfg = load_config_zztj(ZZTJ_CONFIG)
    for entry in zztj_cfg:
        path = derive_paths_zztj(entry, repo_root=repo_root)[0]
        if not path.exists():
            continue
        parsed = parse_file(path)
        ch_id = f"zztj.{entry['juan']}"
        full_text = "\n".join(s.text for s in parsed.segments)
        yield ch_id, full_text


def find_candidates(repo_root: Path, *, existing: set[str]) -> list[Candidate]:
    # primary_name → {courtesy_name → set(chapter_ids)} (occasionally a name's
    # 字 disagrees across chapters; we keep both as separate candidates).
    by_name_courtesy: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for ch_id, full_text in _walk_chapters(repo_root):
        seen_in_chapter: dict[tuple[str, str], int] = defaultdict(int)
        for m in _BIO_RE.finditer(full_text):
            name, courtesy = m.group(1), m.group(2)
            if name in NAME_BLACKLIST or courtesy in NAME_BLACKLIST:
                continue
            if name in existing:
                continue
            if not _is_canonical_traditional(name) or not _is_canonical_traditional(courtesy):
                continue
            seen_in_chapter[(name, courtesy)] += 1
        # Apply repeat-in-chapter filter: name must appear elsewhere in the
        # chapter beyond just the bio-opening match itself.
        for (name, courtesy), _hits in seen_in_chapter.items():
            full_count = full_text.count(name)
            if full_count < MIN_REPEAT_IN_CHAPTER:
                continue
            by_name_courtesy[(name, courtesy)][ch_id] = full_count

    candidates: list[Candidate] = []
    for (name, courtesy), ch_counts in by_name_courtesy.items():
        candidates.append(Candidate(
            primary_name=name,
            courtesy_name=courtesy,
            chapter_ids=sorted(ch_counts.keys()),
            n_occurrences=sum(ch_counts.values()),
        ))
    candidates.sort(key=lambda c: -c.n_occurrences)
    return candidates


def pinyin_id(name: str) -> str:
    """Build a lowercase ascii id from a CJK name via pypinyin."""
    return "".join(lazy_pinyin(name))


def render_yaml_block(candidates: list[Candidate], *, existing_ids: set[str]) -> str:
    """Emit candidates as a YAML snippet ready to be reviewed/appended."""
    lines = ["# === auto-extracted bio-subject candidates ===",
             "# Review carefully — heuristic from `X字Y，` patterns. May include false positives.",
             ""]
    used = set(existing_ids)
    for c in candidates:
        pid = pinyin_id(c.primary_name)
        # Disambiguate if id collides (e.g., already-existing 劉表 vs new 劉表 doppelganger)
        original_pid = pid
        suffix = 2
        while pid in used:
            pid = f"{original_pid}_{suffix}"
            suffix += 1
        used.add(pid)
        lines.append(f"- id: {pid}")
        lines.append(f"  primary_name: {c.primary_name}")
        lines.append(f"  courtesy_name: {c.courtesy_name}")
        lines.append(f"  birth_ad: null")
        lines.append(f"  death_ad: null")
        lines.append(f"  brief: ''  # auto-extracted; FIXME: add brief")
        lines.append(f"  bio_chapters: [{c.best_bio_chapter}]"
                     f"   # found in: {', '.join(c.chapter_ids)} ({c.n_occurrences} total occurrences)")
        lines.append("")
    return "\n".join(lines)


def _existing_ids(people_yaml: Path) -> set[str]:
    if not people_yaml.exists():
        return set()
    data = yaml.safe_load(people_yaml.read_text(encoding="utf-8")) or []
    return {p["id"] for p in data}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Auto-extract bio subject candidates from 列傳 text.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--people", type=Path, default=PEOPLE_YAML)
    p.add_argument("--limit", type=int, default=None,
                   help="show only the top-N candidates by occurrence count")
    args = p.parse_args(argv)

    existing = _existing_names(args.people)
    print(f"# {len(existing)} primary_name + alias entries already in {args.people}",
          file=sys.stderr)

    candidates = find_candidates(args.repo_root, existing=existing)
    if args.limit:
        candidates = candidates[:args.limit]
    print(f"# {len(candidates)} candidate(s) found (≥{MIN_REPEAT_IN_CHAPTER} repeats per chapter)",
          file=sys.stderr)

    print(render_yaml_block(candidates, existing_ids=_existing_ids(args.people)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
