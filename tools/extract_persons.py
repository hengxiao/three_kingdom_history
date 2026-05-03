"""Build per-segment person annotations and merge into annotations/<work>/<book>/<NN>.yaml.

Two-pass per segment:

  Pass 1 — High-confidence forms. For every person, find each occurrence of
           `primary_name` and each `alias` in the segment. Each match is emitted
           as a `type: person` annotation with `via: primary` or `via: alias`.

  Pass 2 — Contextual `given_name` (single-character given names like 操/權/亮).
           Only enabled for persons that already had a Pass 1 hit in the same
           segment, and only matches positions AFTER the first such hit.
           Per-person `given_name_blockers` filters out compound words
           (操行, 權力, 風雲 …) so the single char is only treated as a person
           reference when context allows.

Idempotent: existing entries of `type: person` are dropped and re-emitted.

Run after `tools.extract_annotations*` and `tools.extract_dates*` so the
annotations YAML files already exist; this script merges person entries on top.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import yaml

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
from tools.extract_annotations import annotations_path as annotations_path_sanguozhi
from tools.extract_annotations_hhs import annotations_path as annotations_path_hhs
from tools.extract_dates_zztj import annotations_path as annotations_path_zztj
from tools.segment import parse_file

CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "people.yaml"

_ANCHOR_PARSE_RE = re.compile(r"^([a-z]+)\.(\d+)\.p(\d+)([a-z]*)$")


def _anchor_sort_key(anchor: str) -> tuple:
    m = _ANCHOR_PARSE_RE.match(anchor)
    if not m:
        return ("", 0, 0, "")
    return (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))


def _type_sort_rank(t: str) -> int:
    # 裴注/lixian first (a*), then temporal (t*), then person (h*) at same `at`.
    return {"pei": 0, "lixian": 0, "temporal": 1, "person": 2}.get(t, 3)


@dataclass
class PersonsResult:
    chapter_id: str
    annotations_path: Path
    n_persons: int


@dataclass
class PersonsError:
    entry: dict
    message: str


def load_people_config(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"people config must be a YAML list, got {type(data).__name__}")
    seen = set()
    for p in data:
        if "id" not in p or "primary_name" not in p:
            raise ValueError(f"person entry missing id or primary_name: {p}")
        if p["id"] in seen:
            raise ValueError(f"duplicate person id: {p['id']}")
        seen.add(p["id"])
    return data


def find_person_spans_in_segment(
    seg_text: str,
    persons: list[dict],
    *,
    book: str | None = None,
) -> list[dict]:
    """Return all person spans (Pass 1 + Pass 2) for a single segment text.

    Each span is a dict: {at, length, person_id, text, via}. Spans never overlap
    (Pass 2 skips any position already inside a Pass 1 span, including spans
    belonging to a different person).

    `book` is the chapter's book id (wei/shu/wu/hhs/zztj). It enables
    `chapter_aliases` entries on persons: an alias listed under
    `chapter_aliases[book]` is included in Pass 1 only when the segment lives
    in that book. This is how 廟號/諡號 (太祖, 武皇帝) get matched in 魏書 only,
    where they unambiguously refer to 曹操.
    """
    # ---- Pass 1: primary_name + aliases (+ chapter-scoped aliases), longest-first ----
    patterns: list[tuple[str, str, str]] = []
    for p in persons:
        patterns.append((p["primary_name"], p["id"], "primary"))
        for alias in (p.get("aliases") or []):
            patterns.append((alias, p["id"], "alias"))
        # chapter_aliases: {book_id: [name, ...]}. Only enable when this segment's book matches.
        ch_aliases = p.get("chapter_aliases") or {}
        if book and book in ch_aliases:
            for alias in ch_aliases[book]:
                patterns.append((alias, p["id"], "chapter_alias"))
    patterns.sort(key=lambda x: -len(x[0]))

    spans: list[dict] = []
    pos = 0
    while pos < len(seg_text):
        matched = None
        for pat, pid, via in patterns:
            if seg_text.startswith(pat, pos):
                matched = (pat, pid, via)
                break
        if not matched:
            pos += 1
            continue
        pat, pid, via = matched
        spans.append({"at": pos, "length": len(pat), "person_id": pid,
                      "text": pat, "via": via})
        pos += len(pat)

    # ---- Pass 2: contextual given_name ----
    # given_name matches anywhere in the segment when ANY of the person's full
    # forms (primary/alias/chapter_alias) also appears in the same segment.
    # We don't require given_name to come AFTER the primary — classical
    # narrative often opens a paragraph with the given_name (e.g. 「卓還長安」)
    # and re-establishes the full name later within the same paragraph.
    pass1_ranges = [(sp["at"], sp["at"] + sp["length"]) for sp in spans]
    persons_seen = {sp["person_id"] for sp in spans}

    extras: list[dict] = []
    for p in persons:
        if p["id"] not in persons_seen:
            continue
        gn = p.get("given_name")
        if not gn:
            continue
        blockers = set(p.get("given_name_blockers") or [])
        scan_from = 0
        while scan_from < len(seg_text):
            idx = seg_text.find(gn, scan_from)
            if idx == -1:
                break
            inside_pass1 = any(s <= idx < e for s, e in pass1_ranges)
            if inside_pass1:
                scan_from = idx + len(gn)
                continue
            blocked = False
            if idx > 0 and seg_text[idx - 1:idx + len(gn)] in blockers:
                blocked = True
            if not blocked and idx + len(gn) + 1 <= len(seg_text) \
                    and seg_text[idx:idx + len(gn) + 1] in blockers:
                blocked = True
            if not blocked:
                extras.append({"at": idx, "length": len(gn), "person_id": p["id"],
                               "text": gn, "via": "given_name"})
            scan_from = idx + len(gn)

    # Pass 2 spans must not collide with each other either (different persons
    # sharing a given_name in the future). Sort and dedupe by position.
    spans.extend(extras)
    spans.sort(key=lambda s: (s["at"], -s["length"]))
    deduped: list[dict] = []
    last_end = -1
    for sp in spans:
        if sp["at"] < last_end:
            continue
        deduped.append(sp)
        last_end = sp["at"] + sp["length"]
    return deduped


CROSS_SEGMENT_WINDOW = 8
"""Number of segments after a primary/alias appearance during which that
person's `given_name` will still be matched in following segments — even when
those segments don't re-establish the full name. Classical narrative often
flows the topic across paragraphs (「卓...卓還長安。」). Each new full-name
appearance refreshes the window; passing it lapses the carry-over."""


def build_person_annotations(text_path: Path, persons: list[dict],
                             *, book: str | None = None) -> list[dict]:
    """Walk one chapter's segments and emit person annotation dicts.

    IDs are `<seg_id>.h<N>` numbered per segment in document order.
    `book` enables `chapter_aliases` (廟號/諡號) — see find_person_spans_in_segment.

    Cross-segment carry: after `find_person_spans_in_segment` runs per segment,
    we scan again for `given_name` occurrences in segments that DIDN'T have a
    full-name match for that person, but had one within the prior
    `CROSS_SEGMENT_WINDOW` segments.
    """
    parsed = parse_file(text_path)
    segments = list(parsed.segments)

    # Pass 1+2 per segment.
    per_seg_spans: list[list[dict]] = []
    for seg in segments:
        spans = find_person_spans_in_segment(seg.text, persons, book=book)
        per_seg_spans.append(spans)

    # Cross-segment carry: for each person with a `given_name`, walk the chapter
    # and record the most recent seg index where they had a full-name span.
    # Within the next CROSS_SEGMENT_WINDOW segments, also pick up given_name
    # occurrences that the per-segment pass missed.
    blockers_by_pid = {p["id"]: set(p.get("given_name_blockers") or [])
                       for p in persons if p.get("given_name")}
    gn_by_pid = {p["id"]: p["given_name"] for p in persons if p.get("given_name")}

    last_anchor_at: dict[str, int] = {}
    for i, (seg, spans) in enumerate(zip(segments, per_seg_spans)):
        # First, refresh the window from THIS segment's same-seg matches
        # (primary/alias/chapter_alias). given_name in same-seg also counts —
        # it implies primary appeared in this segment.
        for sp in spans:
            last_anchor_at[sp["person_id"]] = i
        # Find given_name carry-over candidates: persons established within window
        # but NOT having any same-seg match in this segment.
        present_pids = {sp["person_id"] for sp in spans}
        existing_ranges = [(sp["at"], sp["at"] + sp["length"]) for sp in spans]
        for pid, last_i in list(last_anchor_at.items()):
            if pid in present_pids:
                continue
            if i - last_i > CROSS_SEGMENT_WINDOW:
                continue
            gn = gn_by_pid.get(pid)
            if not gn:
                continue
            blockers = blockers_by_pid.get(pid, set())
            scan_from = 0
            carried_any = False
            while scan_from < len(seg.text):
                idx = seg.text.find(gn, scan_from)
                if idx == -1:
                    break
                if any(s <= idx < e for s, e in existing_ranges):
                    scan_from = idx + len(gn)
                    continue
                blocked = False
                if idx > 0 and seg.text[idx - 1:idx + len(gn)] in blockers:
                    blocked = True
                if not blocked and idx + len(gn) + 1 <= len(seg.text) \
                        and seg.text[idx:idx + len(gn) + 1] in blockers:
                    blocked = True
                if not blocked:
                    spans.append({"at": idx, "length": len(gn), "person_id": pid,
                                  "text": gn, "via": "given_name_carry"})
                    existing_ranges.append((idx, idx + len(gn)))
                    carried_any = True
                scan_from = idx + len(gn)
            # Refresh window so subsequent segments can carry from this one too —
            # narrative continuity often spans many paragraphs of bare given_name.
            if carried_any:
                last_anchor_at[pid] = i
        spans.sort(key=lambda s: (s["at"], -s["length"]))
        deduped: list[dict] = []
        last_end = -1
        for sp in spans:
            if sp["at"] < last_end:
                continue
            deduped.append(sp)
            last_end = sp["at"] + sp["length"]
        per_seg_spans[i] = deduped

    out: list[dict] = []
    for seg, spans in zip(segments, per_seg_spans):
        for i, sp in enumerate(spans, start=1):
            out.append({
                "id": f"{seg.id}.h{i}",
                "anchor": seg.id,
                "at": sp["at"],
                "length": sp["length"],
                "type": "person",
                "person_id": sp["person_id"],
                "text": sp["text"],
                "via": sp["via"],
            })
    return out


def merge_persons_into_file(annotations_yaml: Path, persons: list[dict]) -> int:
    if not annotations_yaml.exists():
        raise FileNotFoundError(
            f"{annotations_yaml} missing — run extract_annotations / extract_dates first"
        )
    doc = yaml.safe_load(annotations_yaml.read_text(encoding="utf-8")) or {}
    existing = doc.get("annotations") or []
    keep = [a for a in existing if a.get("type") != "person"]
    merged = keep + list(persons)
    merged.sort(key=lambda a: (
        _anchor_sort_key(str(a.get("anchor", ""))),
        int(a.get("at", 0)),
        _type_sort_rank(a.get("type", "")),
    ))
    doc["annotations"] = merged
    annotations_yaml.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return len(persons)


# ---- Per-work driver registration ----

@dataclass
class WorkDriver:
    name: str
    config_path: Path
    load_config: callable
    derive_paths: callable
    annotations_path: callable
    book_for_entry: callable = None

    def book_of(self, entry: dict) -> str:
        return self.book_for_entry(entry) if self.book_for_entry else entry["book"]


WORKS = [
    WorkDriver(
        name="sanguozhi",
        config_path=SANGUOZHI_CONFIG,
        load_config=load_config_sanguozhi,
        derive_paths=derive_paths_sanguozhi,
        annotations_path=annotations_path_sanguozhi,
    ),
    WorkDriver(
        name="houhanshu",
        config_path=HHS_CONFIG,
        load_config=load_config_hhs,
        derive_paths=derive_paths_hhs,
        annotations_path=annotations_path_hhs,
        book_for_entry=lambda e: "hhs",
    ),
    WorkDriver(
        name="zztj",
        config_path=ZZTJ_CONFIG,
        load_config=load_config_zztj,
        derive_paths=derive_paths_zztj,
        annotations_path=annotations_path_zztj,
        book_for_entry=lambda e: "zztj",
    ),
]


def process_one(entry: dict, *, work: WorkDriver, persons: list[dict],
                repo_root: Path) -> PersonsResult:
    text_path = work.derive_paths(entry, repo_root=repo_root)[0]
    if not text_path.exists():
        raise FileNotFoundError(f"{text_path} missing — fetch the chapter first")
    book = work.book_of(entry)
    chapter_id = f"{book}.{int(entry['juan'])}"
    out_path = work.annotations_path(entry, repo_root=repo_root)
    person_anns = build_person_annotations(text_path, persons, book=book)
    n = merge_persons_into_file(out_path, person_anns)
    return PersonsResult(chapter_id=chapter_id, annotations_path=out_path, n_persons=n)


def run(*, repo_root: Path = REPO_ROOT_DEFAULT,
        people_config: Path = CONFIG_PATH_DEFAULT,
        works: list[WorkDriver] | None = None) -> Iterator[PersonsResult | PersonsError]:
    persons = load_people_config(people_config)
    for work in (works if works is not None else WORKS):
        cfg = work.load_config(work.config_path)
        for entry in cfg:
            try:
                yield process_one(entry, work=work, persons=persons, repo_root=repo_root)
            except FileNotFoundError as e:
                # Missing text or annotations file — skip silently (chapter not yet fetched/extracted).
                yield PersonsError(entry={**entry, "_work": work.name},
                                   message=str(e))
            except Exception as e:  # noqa: BLE001
                yield PersonsError(entry={**entry, "_work": work.name},
                                   message=f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract person annotations into annotations/.")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    p.add_argument("--people", type=Path, default=CONFIG_PATH_DEFAULT)
    args = p.parse_args(argv)

    n_ok = n_err = 0
    n_total = 0
    for r in run(repo_root=args.repo_root, people_config=args.people):
        if isinstance(r, PersonsError):
            n_err += 1
            print(f"SKIP {r.entry.get('_work','?')}/{r.entry.get('book','?')}.{r.entry.get('juan','?')}: {r.message}",
                  file=sys.stderr)
        else:
            n_ok += 1
            n_total += r.n_persons
            print(f"OK   {r.chapter_id}: {r.n_persons} person anns → {r.annotations_path}")
    print(f"\n{n_ok} chapters, {n_total} person annotations total, {n_err} skipped",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
