"""Generate variant ops between canonical and a source text per doc/format.md §4.4."""
from __future__ import annotations

import difflib
from typing import Iterable

from tools.segment import normalized_hash


# Each op is a dict so it serializes naturally to YAML. Shapes:
#   {"op": "replace", "at": int, "length": int, "from": str, "to": str}
#   {"op": "insert",  "at": int, "text": str}
#   {"op": "delete",  "at": int, "length": int, "text": str}


def compute_ops(canonical: str, source: str) -> list[dict]:
    """Return the minimal list of ops that transform `canonical` into `source`."""
    matcher = difflib.SequenceMatcher(a=canonical, b=source, autojunk=False)
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            ops.append({
                "op": "replace",
                "at": i1,
                "length": i2 - i1,
                "from": canonical[i1:i2],
                "to": source[j1:j2],
            })
        elif tag == "delete":
            ops.append({
                "op": "delete",
                "at": i1,
                "length": i2 - i1,
                "text": canonical[i1:i2],
            })
        elif tag == "insert":
            ops.append({
                "op": "insert",
                "at": i1,
                "text": source[j1:j2],
            })
        else:
            raise ValueError(f"unknown opcode from difflib: {tag!r}")
    return ops


def apply_ops(canonical: str, ops: Iterable[dict]) -> str:
    """Inverse of compute_ops: apply ops in order to canonical, return source."""
    ops = list(ops)
    _validate_ops(canonical, ops)
    out: list[str] = []
    cursor = 0
    for o in ops:
        at = o["at"]
        out.append(canonical[cursor:at])
        kind = o["op"]
        if kind == "replace":
            out.append(o["to"])
            cursor = at + o["length"]
        elif kind == "delete":
            cursor = at + o["length"]
        elif kind == "insert":
            out.append(o["text"])
            cursor = at
        else:
            raise ValueError(f"unknown op kind: {kind!r}")
    out.append(canonical[cursor:])
    return "".join(out)


def _validate_ops(canonical: str, ops: list[dict]) -> None:
    last = 0
    for o in ops:
        at = o["at"]
        if at < last:
            raise ValueError(f"ops not in order or overlap (at={at}, last_end={last}): {o}")
        if at > len(canonical):
            raise ValueError(f"op position {at} past canonical length {len(canonical)}")
        if o["op"] in ("replace", "delete"):
            length = o["length"]
            if at + length > len(canonical):
                raise ValueError(f"op {o} extends past canonical length {len(canonical)}")
            if o["op"] == "replace" and canonical[at:at + length] != o["from"]:
                raise ValueError(f"replace op 'from' does not match canonical at {at}: {o}")
            if o["op"] == "delete" and canonical[at:at + length] != o["text"]:
                raise ValueError(f"delete op 'text' does not match canonical at {at}: {o}")
            last = at + length
        elif o["op"] == "insert":
            last = at
        else:
            raise ValueError(f"unknown op kind: {o.get('op')!r}")


def classify(canonical: str, source: str) -> dict:
    """Return {kind, equal_normalized, ops} per doc/format.md §4.3.

    `kind` is one of: equal | variant_char | textual.
    Caller may override with more specific kinds (typo, missing, extra, punctuation).
    """
    if canonical == source:
        return {"kind": "equal", "equal_normalized": True, "ops": []}
    ops = compute_ops(canonical, source)
    eq_norm = normalized_hash(canonical) == normalized_hash(source)
    kind = "variant_char" if eq_norm else "textual"
    return {"kind": kind, "equal_normalized": eq_norm, "ops": ops}
