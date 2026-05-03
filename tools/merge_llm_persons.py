"""Read LLM-produced person disambiguation outputs and merge them into the
existing annotations YAML files as `type: person, via: llm` annotations.

Expected output JSON shape (per chapter, written by the LLM agent):

  {
    "chapter_id": "wei.1",
    "decisions": [
      {
        "anchor": "wei.1.p5",
        "at": 39,
        "length": 2,
        "surface": "太子",
        "person_id": "liubian",
        "confidence": 0.95,
        "reasoning": "..."
      },
      ...
    ]
  }

Defensive validation:
  - person_id must exist in roster (people.yaml)
  - anchor must be a real segment id in the chapter
  - text[at:at+length] must equal `surface` (catches off-by-one + agent
    hallucinations of position)
  - confidence ≥ MIN_CONFIDENCE (default 0.7)
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
INPUT_DIR_DEFAULT = REPO_ROOT_DEFAULT / "site" / "data" / "llm" / "inputs"
OUTPUT_DIR_DEFAULT = REPO_ROOT_DEFAULT / "site" / "data" / "llm" / "outputs"

MIN_CONFIDENCE = 0.7

_ANCHOR_PARSE_RE = re.compile(r"^([a-z]+)\.(\d+)\.p(\d+)([a-z]*)$")


def _anchor_sort_key(anchor: str) -> tuple:
    m = _ANCHOR_PARSE_RE.match(anchor)
    if not m:
        return ("", 0, 0, "")
    return (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))


def _type_sort_rank(t: str) -> int:
    return {"pei": 0, "lixian": 0, "temporal": 1, "person": 2}.get(t, 3)


def _annotations_yaml_for(work: str, book: str, juan: int, repo_root: Path) -> Path:
    if work == "sanguozhi":
        return repo_root / "annotations" / "sanguozhi" / book / f"{juan:02d}.yaml"
    if work == "houhanshu":
        return repo_root / "annotations" / "houhanshu" / f"{juan:02d}.yaml"
    if work == "zztj":
        return repo_root / "annotations" / "zztj" / f"{juan:03d}.yaml"
    raise ValueError(f"unknown work: {work}")


def _load_chapter_data(work: str, book: str, juan: int, repo_root: Path) -> dict:
    p = repo_root / "site" / "data" / work / book / f"{juan:02d}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _load_input(input_path: Path) -> dict:
    return json.loads(input_path.read_text(encoding="utf-8"))


def validate_decision(decision: dict, *, seg_by_id: dict, roster_ids: set) -> str | None:
    """Return error message if the decision should be skipped; None if OK."""
    anchor = decision.get("anchor")
    if not anchor or anchor not in seg_by_id:
        return f"unknown anchor {anchor!r}"
    pid = decision.get("person_id")
    if pid is None:
        return "person_id is null (LLM declined)"
    if pid not in roster_ids:
        return f"unknown person_id {pid!r}"
    at = decision.get("at")
    length = decision.get("length", 0)
    surface = decision.get("surface")
    if not isinstance(at, int) or not isinstance(length, int) or length <= 0:
        return f"invalid at/length: {at}/{length}"
    seg_text = seg_by_id[anchor]["text"]
    if at < 0 or at + length > len(seg_text):
        return f"position out of range: at={at} length={length} seg_len={len(seg_text)}"
    actual = seg_text[at:at + length]
    if actual != surface:
        return f"surface mismatch: expected {surface!r} got {actual!r}"
    confidence = decision.get("confidence", 1.0)
    if isinstance(confidence, (int, float)) and confidence < MIN_CONFIDENCE:
        return f"confidence {confidence} below {MIN_CONFIDENCE}"
    return None


def merge_one_chapter(output_path: Path, *, repo_root: Path,
                      roster_ids: set, dry_run: bool = False) -> dict:
    """Merge one LLM output file into the chapter's annotations YAML.

    Returns a stats dict. Skipped/invalid decisions are reported but don't fail.
    """
    out_doc = json.loads(output_path.read_text(encoding="utf-8"))
    chapter_id = out_doc["chapter_id"]
    decisions = out_doc.get("decisions", [])

    # Resolve work/book/juan from the chapter_id by reading the site index.
    index = json.loads((repo_root / "site" / "data" / "index.json").read_text(encoding="utf-8"))
    work = book = None
    juan = 0
    for b in index["books"]:
        for c in b["chapters"]:
            if c["id"] == chapter_id:
                work = b["work_id"]
                book = b["id"]
                juan = int(c["juan"])
                break
        if work:
            break
    if not work:
        raise ValueError(f"chapter {chapter_id} not found in site index")

    ch = _load_chapter_data(work, book, juan, repo_root)
    seg_by_id = {s["id"]: s for s in ch["segments"]}

    # Existing annotations keyed by (anchor, at) so we don't double-emit.
    existing_keys: set[tuple[str, int]] = set()
    for s in ch["segments"]:
        for a in s.get("annotations", []):
            if a.get("type") == "person":
                existing_keys.add((s["id"], a["at"]))

    new_anns: list[dict] = []
    n_accepted = n_rejected = n_dup = 0
    rejections: list[str] = []
    for d in decisions:
        err = validate_decision(d, seg_by_id=seg_by_id, roster_ids=roster_ids)
        if err:
            n_rejected += 1
            rejections.append(f"{d.get('anchor')}@{d.get('at')}: {err}")
            continue
        anchor = d["anchor"]
        at = d["at"]
        if (anchor, at) in existing_keys:
            n_dup += 1
            continue
        existing_keys.add((anchor, at))
        # Build annotation entry. id will be assigned during merge into YAML.
        new_anns.append({
            "anchor": anchor,
            "at": at,
            "length": d["length"],
            "type": "person",
            "person_id": d["person_id"],
            "text": d["surface"],
            "via": "llm",
            "confidence": float(d.get("confidence", 1.0)),
            "reasoning": d.get("reasoning", ""),
        })
        n_accepted += 1

    if dry_run or not new_anns:
        return {
            "chapter_id": chapter_id,
            "accepted": n_accepted,
            "rejected": n_rejected,
            "duplicate": n_dup,
            "rejections": rejections,
        }

    # Merge into the chapter's annotations YAML, assigning .h<N+i> ids per anchor.
    yaml_path = _annotations_yaml_for(work, book, juan, repo_root)
    if not yaml_path.exists():
        raise FileNotFoundError(f"{yaml_path} missing — re-run extract_persons first")
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    existing = list(doc.get("annotations") or [])

    # Compute next h-index per anchor (current max + 1).
    h_counter: dict[str, int] = {}
    for a in existing:
        if a.get("type") == "person" and "id" in a:
            m = re.match(r"^.+\.h(\d+)$", a["id"])
            if m:
                anc = a["anchor"]
                h_counter[anc] = max(h_counter.get(anc, 0), int(m.group(1)))

    for a in new_anns:
        anc = a["anchor"]
        h_counter[anc] = h_counter.get(anc, 0) + 1
        a["id"] = f"{anc}.h{h_counter[anc]}"

    merged = existing + new_anns
    merged.sort(key=lambda a: (
        _anchor_sort_key(str(a.get("anchor", ""))),
        int(a.get("at", 0)),
        _type_sort_rank(a.get("type", "")),
    ))
    doc["annotations"] = merged
    yaml_path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return {
        "chapter_id": chapter_id,
        "accepted": n_accepted,
        "rejected": n_rejected,
        "duplicate": n_dup,
        "rejections": rejections,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Merge LLM-disambiguated person annotations into annotations YAML.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--people", type=Path, default=PEOPLE_YAML)
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT)
    p.add_argument("--only", type=str, default=None,
                   help="comma-separated chapter ids to merge")
    p.add_argument("--dry-run", action="store_true",
                   help="don't write YAML, just report stats")
    args = p.parse_args(argv)

    roster = yaml.safe_load(args.people.read_text(encoding="utf-8")) or []
    roster_ids = {p["id"] for p in roster}

    only = set((args.only or "").split(",")) if args.only else None
    files = sorted(args.output_dir.glob("*.json"))
    n_chapters = 0
    n_accepted = n_rejected = n_dup = 0
    for f in files:
        ch_id = f.stem
        if only and ch_id not in only:
            continue
        n_chapters += 1
        stats = merge_one_chapter(f, repo_root=args.repo_root,
                                  roster_ids=roster_ids, dry_run=args.dry_run)
        n_accepted += stats["accepted"]
        n_rejected += stats["rejected"]
        n_dup += stats["duplicate"]
        verb = "would accept" if args.dry_run else "merged"
        print(f"{ch_id}: {verb} {stats['accepted']}, rejected {stats['rejected']}, dup {stats['duplicate']}",
              file=sys.stderr)
        for r in stats["rejections"][:3]:
            print(f"    rejection: {r}", file=sys.stderr)
    print(f"\n{n_chapters} chapter(s); {n_accepted} accepted, {n_rejected} rejected, {n_dup} dup",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
