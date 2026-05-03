"""For each null decision in `site/data/llm/outputs/*.json`, look at the agent's
`reasoning` text and try to identify the person against the (now-expanded) roster.

The Tier 2 agents wrote descriptive reasoning like:
    "桓帝懿獻梁皇后 (梁女瑩) — 廢黜後憂死"
even when they couldn't resolve to a roster id (because the roster was stale at
input-generation time). After expanding the roster, many of those descriptions
now match `primary_name` or `aliases` of new entries.

Strategy:
  - For each roster entry, build a list of "lookup tokens": primary_name +
    each alias + each chapter_alias (for any book).
  - For each null decision, scan the reasoning text for matches. If exactly
    one roster entry matches AND the surface is plausible for that entry
    (an empress matches 太后/皇后, a prince matches 太子/王后, etc.),
    rewrite the decision in-place with that person_id.

Rewrites happen on the OUTPUT file. After running this, re-run merge_llm_persons.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
PEOPLE_YAML = Path(__file__).resolve().parent / "people.yaml"
OUTPUT_DIR_DEFAULT = REPO_ROOT_DEFAULT / "site" / "data" / "llm" / "outputs"

# Surface → expected role keyword (one of these must appear in roster entry's
# brief or primary_name for the match to be plausible).
ROLE_HINTS = {
    "太子": ["太子", "嗣子", "皇太子"],
    "太后": ["太后", "皇后", "夫人", "皇太后"],
    "皇后": ["皇后", "夫人", "貴人", "美人"],
    "陛下": ["皇帝", "帝", "皇", "天子", "公"],
    "主上": ["皇帝", "帝", "皇", "主", "公"],
    "王后": ["皇后", "夫人", "王后"],
}


def _build_lookup(people_yaml: Path) -> list[tuple[list[str], str, str]]:
    """Return list of (tokens, person_id, brief_text). Tokens are sorted longest-first
    within each entry so partial matches don't overshadow specific ones."""
    data = yaml.safe_load(people_yaml.read_text(encoding="utf-8"))
    out = []
    for p in data:
        tokens = [p["primary_name"]]
        for a in (p.get("aliases") or []):
            if a:
                tokens.append(a)
        for vs in (p.get("chapter_aliases") or {}).values():
            tokens.extend(vs)
        # Drop one-char tokens (太/王/帝 alone match too aggressively).
        tokens = [t for t in tokens if t and len(t) >= 2]
        # Dedupe preserving order.
        seen = set()
        unique = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        unique.sort(key=lambda x: -len(x))
        out.append((unique, p["id"], p.get("brief", "") + p["primary_name"]))
    return out


def find_matches_in_reasoning(reasoning: str, surface: str,
                              lookup: list[tuple[list[str], str, str]]) -> list[tuple[str, str]]:
    """Return list of (person_id, matched_token) where the reasoning mentions
    that person AND the role hint matches the surface."""
    if not reasoning:
        return []
    role_words = ROLE_HINTS.get(surface, [])
    out = []
    for tokens, pid, brief in lookup:
        # Only consider entries whose brief/name contains a role keyword
        # consistent with this surface.
        if role_words and not any(w in brief for w in role_words):
            continue
        for token in tokens:
            if token in reasoning:
                out.append((pid, token))
                break
    return out


def resolve_one_file(output_path: Path,
                     lookup: list[tuple[list[str], str, str]]) -> dict:
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    decisions = doc.get("decisions") or []
    n_resolved = n_ambiguous = n_still_null = 0
    for d in decisions:
        if d.get("person_id") is not None:
            continue   # already resolved
        reasoning = d.get("reasoning") or ""
        surface = d.get("surface") or ""
        matches = find_matches_in_reasoning(reasoning, surface, lookup)
        # Keep only unique person_ids
        unique_pids = sorted({pid for pid, _ in matches})
        if len(unique_pids) == 1:
            d["person_id"] = unique_pids[0]
            d["confidence"] = max(0.7, float(d.get("confidence") or 0.7))
            d["via_resolver"] = "reasoning_lookup"
            n_resolved += 1
        elif len(unique_pids) > 1:
            n_ambiguous += 1
            d["resolver_note"] = f"ambiguous between {unique_pids}"
        else:
            n_still_null += 1
    output_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return {"resolved": n_resolved, "ambiguous": n_ambiguous, "still_null": n_still_null}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Resolve null LLM decisions using agent reasoning + expanded roster.")
    p.add_argument("--people", type=Path, default=PEOPLE_YAML)
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT)
    args = p.parse_args(argv)

    lookup = _build_lookup(args.people)
    print(f"loaded lookup tokens for {len(lookup)} roster entries", file=sys.stderr)

    files = sorted(args.output_dir.glob("*.json"))
    total_resolved = total_ambiguous = total_still_null = 0
    for f in files:
        stats = resolve_one_file(f, lookup)
        total_resolved += stats["resolved"]
        total_ambiguous += stats["ambiguous"]
        total_still_null += stats["still_null"]
        if stats["resolved"] or stats["ambiguous"]:
            print(f"  {f.stem}: resolved {stats['resolved']}, ambiguous {stats['ambiguous']}, still_null {stats['still_null']}",
                  file=sys.stderr)
    print(f"\n{len(files)} files; +{total_resolved} resolved, {total_ambiguous} ambiguous, {total_still_null} still null",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
