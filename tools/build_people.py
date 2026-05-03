"""Build site/data/people.json + per-person JSON from tools/people.yaml.

Reads person annotations produced by `tools.extract_persons` from each
chapter's annotations YAML. Mentions are 1-per-(chapter, segment) where any
person annotation for the target person id appears.

    site/data/people.json           ← directory of all persons (sorted by n_mentions desc)
    site/data/people/<id>.json      ← per-person: bio chapters + grouped mentions

Per `tools/people.yaml`:
  - `aliases` are high-confidence search patterns (mostly the unique 字 form).
  - `given_name` is the single-character name; matched only after primary
    appears in the same segment, with `given_name_blockers` filtering compound
    words. (Both happen at extract time.)
  - `other_names` are display-only; not searched, since they collide across
    people (e.g. 太祖 in 魏书 = 曹操 but in 吳书 = 孫權).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from tools.segment import parse_file

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "people.yaml"


@dataclass
class ChapterIndex:
    """One chapter loaded into memory: segments + person annotations from the YAML."""
    chapter_id: str           # "wei.1" / "hhs.8" / "zztj.56"
    work: str                 # "sanguozhi" | "houhanshu" | "zztj"
    book: str                 # "wei"|"shu"|"wu"|"hhs"|"zztj"
    juan: int
    book_title: str
    chapter_title: str
    segments: list            # list of Segment objects (id + text)
    person_anns: list = field(default_factory=list)
    """Each ann: {person_id, anchor, at, length, text, via}. Loaded from
    annotations/<work>/<book>/<NN>.yaml — populated by tools.extract_persons."""


def load_config(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"config must be a YAML list, got {type(data).__name__}")
    seen_ids = set()
    for entry in data:
        if "id" not in entry or "primary_name" not in entry:
            raise ValueError(f"person entry missing id or primary_name: {entry}")
        if entry["id"] in seen_ids:
            raise ValueError(f"duplicate person id: {entry['id']}")
        seen_ids.add(entry["id"])
    return data


def _chapter_data_url(work: str, book: str, juan: int) -> str:
    return f"data/{work}/{book}/{juan:02d}.json"


def _walk_text_files(repo_root: Path) -> Iterable[tuple[str, str, int, Path]]:
    """Yield (work, book, juan, md_path) for every chapter markdown file."""
    sg = repo_root / "texts" / "sanguozhi"
    if sg.exists():
        for book_dir in sorted(sg.iterdir()):
            if not book_dir.is_dir():
                continue
            for f in sorted(book_dir.glob("*.md")):
                yield ("sanguozhi", book_dir.name, int(f.stem), f)
    hhs = repo_root / "texts" / "houhanshu"
    if hhs.exists():
        for f in sorted(hhs.glob("*.md")):
            yield ("houhanshu", "hhs", int(f.stem), f)
    zztj = repo_root / "texts" / "zztj"
    if zztj.exists():
        for f in sorted(zztj.glob("*.md")):
            yield ("zztj", "zztj", int(f.stem), f)


def _annotations_yaml_for(work: str, book: str, juan: int, repo_root: Path) -> Path:
    """Mirror the per-work `annotations_path` resolvers without importing all of them."""
    if work == "sanguozhi":
        return repo_root / "annotations" / "sanguozhi" / book / f"{juan:02d}.yaml"
    if work == "houhanshu":
        return repo_root / "annotations" / "houhanshu" / f"{juan:02d}.yaml"
    if work == "zztj":
        return repo_root / "annotations" / "zztj" / f"{juan:03d}.yaml"
    raise ValueError(f"unknown work: {work}")


def _load_person_annotations(yaml_path: Path) -> list[dict]:
    if not yaml_path.exists():
        return []
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    return [a for a in (doc.get("annotations") or []) if a.get("type") == "person"]


def load_all_chapters(repo_root: Path) -> dict[str, ChapterIndex]:
    """Parse every chapter and return {chapter_id: ChapterIndex} with person annotations attached."""
    out: dict[str, ChapterIndex] = {}
    for work, book, juan, path in _walk_text_files(repo_root):
        parsed = parse_file(path)
        fm = parsed.frontmatter
        chapter_id = f"{book}.{juan}"
        person_anns = _load_person_annotations(
            _annotations_yaml_for(work, book, juan, repo_root)
        )
        out[chapter_id] = ChapterIndex(
            chapter_id=chapter_id,
            work=work, book=book, juan=juan,
            book_title=fm.get("book_title", book),
            chapter_title=fm.get("title", ""),
            segments=list(parsed.segments),
            person_anns=person_anns,
        )
    return out


def _make_snippet(seg_text: str, at: int, *, before: int = 25, after: int = 65) -> str:
    start = max(0, at - before)
    end = min(len(seg_text), at + after)
    out = seg_text[start:end]
    if start > 0:
        out = "…" + out
    if end < len(seg_text):
        out = out + "…"
    return out


def find_mentions_for_person(
    person: dict,
    chapters: dict[str, ChapterIndex],
    *,
    skip_chapter_ids: set[str],
) -> list[dict]:
    """Return one mention per (chapter, segment) where a person annotation hits.

    Per-segment dedup: if a segment has multiple annotations for this person
    (e.g., 曹操 + 操), record one mention pointing at the FIRST annotation, with
    `matched` set to the longest annotation's surface (most specific form).
    """
    pid = person["id"]
    seg_text_by_id = {seg.id: seg.text for ch in chapters.values() for seg in ch.segments}

    # Group annotations by (chapter_id, anchor)
    by_seg: dict[tuple[str, str], list[dict]] = {}
    for chapter_id, ch in chapters.items():
        if chapter_id in skip_chapter_ids:
            continue
        for ann in ch.person_anns:
            if ann.get("person_id") != pid:
                continue
            by_seg.setdefault((chapter_id, ann["anchor"]), []).append(ann)

    out: list[dict] = []
    for (chapter_id, anchor), anns in by_seg.items():
        ch = chapters[chapter_id]
        anns.sort(key=lambda a: a["at"])
        first = anns[0]
        # Pick the longest surface as the recorded matched form (e.g., 曹操 over 操).
        longest = max(anns, key=lambda a: a.get("length", 0))
        seg_text = seg_text_by_id.get(anchor, "")
        out.append({
            "chapter_id": chapter_id,
            "chapter_title": ch.chapter_title,
            "book_title": ch.book_title,
            "work": ch.work,
            "anchor": anchor,
            "at": first["at"],
            "data_url": _chapter_data_url(ch.work, ch.book, ch.juan),
            "matched": longest.get("text", ""),
            "snippet": _make_snippet(seg_text, first["at"]) if seg_text else "",
        })
    return out


def build_one_person(person: dict, chapters: dict[str, ChapterIndex]) -> dict:
    bio_ids = list(person.get("bio_chapters") or [])
    bio_chapters_out = []
    for cid in bio_ids:
        ch = chapters.get(cid)
        if not ch:
            continue
        bio_chapters_out.append({
            "chapter_id": cid,
            "title": ch.chapter_title,
            "book_title": ch.book_title,
            "work": ch.work,
            "data_url": _chapter_data_url(ch.work, ch.book, ch.juan),
        })
    mentions = find_mentions_for_person(person, chapters, skip_chapter_ids=set(bio_ids))

    # Group + sort: 资治通鉴 first (chronicle backbone), then bios, in chapter order.
    work_priority = {"zztj": 0, "sanguozhi": 1, "houhanshu": 2}
    mentions.sort(key=lambda m: (
        work_priority.get(m["work"], 99),
        m["chapter_id"], m["anchor"], m["at"],
    ))
    mentions_by_work = {"zztj": [], "sanguozhi": [], "houhanshu": []}
    for m in mentions:
        mentions_by_work.setdefault(m["work"], []).append(m)

    return {
        "id": person["id"],
        "primary_name": person["primary_name"],
        "courtesy_name": person.get("courtesy_name"),
        "given_name": person.get("given_name"),
        "aliases": list(person.get("aliases") or []),
        "other_names": list(person.get("other_names") or []),
        "birth_ad": person.get("birth_ad"),
        "death_ad": person.get("death_ad"),
        "brief": person.get("brief", ""),
        "bio_chapters": bio_chapters_out,
        "mentions_by_work": mentions_by_work,
        "n_mentions": len(mentions),
    }


def build_index_dict(persons: list[dict]) -> dict:
    rows = []
    for p in persons:
        rows.append({
            "id": p["id"],
            "primary_name": p["primary_name"],
            "courtesy_name": p.get("courtesy_name"),
            "brief": p.get("brief", ""),
            "birth_ad": p.get("birth_ad"),
            "death_ad": p.get("death_ad"),
            "n_bio_chapters": len(p["bio_chapters"]),
            "n_mentions": p["n_mentions"],
        })
    rows.sort(key=lambda r: (-r["n_mentions"], r["id"]))

    # Flat name → person_id table for the site to wire person-name links into
    # any rendered text. Same conservative pattern set as the search index
    # (primary_name + aliases only; other_names stays display-only).
    # Sorted longest-first so e.g. "諸葛亮" matches before something shorter would.
    name_pairs: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for p in persons:
        for name in [p["primary_name"]] + list(p.get("aliases") or []):
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            name_pairs.append((name, p["id"]))
    name_pairs.sort(key=lambda x: -len(x[0]))
    return {
        "generated_by": "tools/build_people.py",
        "people": rows,
        "name_index": [[name, pid] for name, pid in name_pairs],
    }


def write_all(*, repo_root: Path = REPO_ROOT_DEFAULT,
              config_path: Path = CONFIG_PATH_DEFAULT) -> list[dict]:
    config = load_config(config_path)
    chapters = load_all_chapters(repo_root)
    out_dir = repo_root / "site" / "data" / "people"
    out_dir.mkdir(parents=True, exist_ok=True)

    built: list[dict] = []
    for entry in config:
        p = build_one_person(entry, chapters)
        (out_dir / f"{p['id']}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        built.append(p)

    index = build_index_dict(built)
    (repo_root / "site" / "data" / "people.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return built


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build people.json + per-person JSON from tools/people.yaml.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--config", type=Path, default=CONFIG_PATH_DEFAULT)
    args = p.parse_args(argv)
    built = write_all(repo_root=args.repo_root, config_path=args.config)
    print(f"wrote {len(built)} person JSON files + people.json (sorted by mention count)",
          file=sys.stderr)
    for r in sorted(built, key=lambda x: -x["n_mentions"])[:10]:
        print(f"  {r['primary_name']:>4}: {r['n_mentions']:>4} mentions, {len(r['bio_chapters'])} bios",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
