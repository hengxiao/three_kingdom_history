"""Find ambiguous person-reference markers (太子/太后/皇后/陛下/主上/王后) and
emit per-chapter input JSON for an LLM disambiguation pass.

These markers are unambiguous category-wise (they always refer to a person),
but they don't resolve to a fixed person id without contextual knowledge of
the era, book, and recent narrative. Tier 1 (deterministic chapter_aliases)
can't handle them — each chapter has a different 太子, a different 太后, etc.

Output layout:

    site/data/llm/inputs/<chapter_id>.json    # one per chapter w/ candidates
    site/data/llm/outputs/<chapter_id>.json   # filled in by the LLM agent

Each input file contains:
  - chapter metadata (id, work, book, juan, title, book_title)
  - segments[]: every segment in document order with text, existing person
    annotations (so the LLM can avoid double-counting), and `markers[]`
    listing unresolved high-signal positions to disambiguate
  - roster: pruned roster (id + primary_name + courtesy_name + brief +
    bio_chapters + chapter_aliases + birth_ad + death_ad) — enough for the
    LLM to reason about era + identity

The LLM is asked to fill in each marker with a person_id (or `null` if
uncertain or not a person reference) and write to the output path.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
PEOPLE_YAML = Path(__file__).resolve().parent / "people.yaml"

# v1 markers: high-signal (always person references; just need disambiguation).
# 公/上/帝 are deferred to v2 — too many false positives (之上, 黃帝, 主公 etc.).
HIGH_SIGNAL_MARKERS = ["太子", "太后", "皇后", "陛下", "主上", "王后"]


def _load_index(repo_root: Path) -> dict:
    return json.loads((repo_root / "site" / "data" / "index.json").read_text(encoding="utf-8"))


def _load_chapter(repo_root: Path, work: str, book: str, juan: int) -> dict:
    p = repo_root / "site" / "data" / work / book / f"{juan:02d}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _build_roster(people_yaml: Path) -> list[dict]:
    """Compact roster for LLM context: id, name, dates, brief, bio book(s).
    Drop fields the LLM doesn't need (aliases, chapter_aliases, given_name, blockers)
    to keep input size down."""
    data = yaml.safe_load(people_yaml.read_text(encoding="utf-8"))
    out = []
    for p in data:
        out.append({
            "id": p["id"],
            "primary_name": p["primary_name"],
            "courtesy_name": p.get("courtesy_name") or "",
            "birth_ad": p.get("birth_ad"),
            "death_ad": p.get("death_ad"),
            "brief": p.get("brief", ""),
            "bio_chapters": list(p.get("bio_chapters") or []),
        })
    return out


def find_markers_in_segment(seg: dict) -> list[dict]:
    """Return [{at, length, surface, snippet}] for each unresolved high-signal marker
    in `seg`. Position is considered unresolved if no existing person annotation
    overlaps it.
    """
    text = seg["text"]
    person_ranges = [(a["at"], a["at"] + a["length"])
                     for a in seg.get("annotations", []) if a.get("type") == "person"]
    out = []
    for marker in HIGH_SIGNAL_MARKERS:
        for m in re.finditer(marker, text):
            pos = m.start()
            if any(s <= pos < e for s, e in person_ranges):
                continue
            # snippet: ~30 chars around the marker for the LLM's context
            start = max(0, pos - 25)
            end = min(len(text), pos + 25 + len(marker))
            snippet = text[start:end]
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
            out.append({
                "at": pos,
                "length": len(marker),
                "surface": marker,
                "snippet": snippet,
            })
    return out


def build_input_for_chapter(ch: dict, roster: list[dict]) -> dict | None:
    """Build the LLM input dict for one chapter, or return None if there are
    no unresolved markers."""
    segments_out = []
    total_markers = 0
    for seg in ch["segments"]:
        markers = find_markers_in_segment(seg)
        if not markers:
            # Still include the segment (lightweight) so the LLM can reason
            # about the surrounding narrative when disambiguating an adjacent
            # segment's marker. We ship `text` but no `markers`.
            segments_out.append({
                "id": seg["id"],
                "text": seg["text"],
                "existing_persons": [
                    {"at": a["at"], "length": a["length"],
                     "person_id": a["person_id"], "text": a["text"], "via": a["via"]}
                    for a in seg.get("annotations", []) if a.get("type") == "person"
                ],
            })
            continue
        total_markers += len(markers)
        segments_out.append({
            "id": seg["id"],
            "text": seg["text"],
            "existing_persons": [
                {"at": a["at"], "length": a["length"],
                 "person_id": a["person_id"], "text": a["text"], "via": a["via"]}
                for a in seg.get("annotations", []) if a.get("type") == "person"
            ],
            "markers": markers,
        })
    if total_markers == 0:
        return None
    return {
        "chapter": {
            "id": ch["id"],
            "work": ch["work"],
            "book": ch["book"],
            "juan": ch["juan"],
            "title": ch["title"],
            "book_title": ch["book_title"],
        },
        "n_markers": total_markers,
        "segments": segments_out,
        "roster": roster,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build per-chapter LLM input files for ambiguous person disambiguation.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--people", type=Path, default=PEOPLE_YAML)
    p.add_argument("--only", type=str, default=None,
                   help="comma-separated chapter ids (e.g., 'wei.1,zztj.61') to process; default = all")
    args = p.parse_args(argv)

    index = _load_index(args.repo_root)
    roster = _build_roster(args.people)

    out_dir = args.repo_root / "site" / "data" / "llm" / "inputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    only = set((args.only or "").split(",")) if args.only else None
    n_files = n_markers = 0
    for book in index["books"]:
        for entry in book["chapters"]:
            ch_id = entry["id"]
            if only and ch_id not in only:
                continue
            ch = _load_chapter(args.repo_root, book["work_id"], book["id"], int(entry["juan"]))
            payload = build_input_for_chapter(ch, roster)
            if payload is None:
                continue
            (out_dir / f"{ch_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            n_files += 1
            n_markers += payload["n_markers"]
    print(f"wrote {n_files} input file(s) covering {n_markers} unresolved markers → {out_dir}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
