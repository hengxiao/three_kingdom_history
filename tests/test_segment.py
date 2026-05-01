"""Tests for tools/segment.py — covers hash determinism, normalization, parsing."""
from __future__ import annotations

import pytest

from tools.segment import (
    FormatError,
    Segment,
    canonical_hash,
    file_segments_sha256,
    normalized_hash,
    parse_text,
    render_with_frontmatter,
)


# ---------- canonical_hash ----------

def test_canonical_hash_is_deterministic():
    text = "太祖武皇帝，沛國譙人也。"
    assert canonical_hash(text) == canonical_hash(text)


def test_canonical_hash_strips_all_whitespace():
    base = "太祖武皇帝，沛國譙人也。"
    assert canonical_hash(base) == canonical_hash(f"  \n{base}\n  ")
    assert canonical_hash(base) == canonical_hash("太祖武皇帝，\n沛國譙人也。")
    assert canonical_hash(base) == canonical_hash("太祖武皇帝，  沛國譙人也。")
    assert canonical_hash(base) == canonical_hash("太祖武皇帝， 沛國譙人也。")
    # Tabs and full-width spaces should also be stripped.
    assert canonical_hash(base) == canonical_hash("太祖武皇帝，\t沛國譙人也。")
    assert canonical_hash(base) == canonical_hash("太祖武皇帝，　沛國譙人也。")


def test_canonical_hash_is_sensitive_to_punctuation_and_chars():
    a = "太祖武皇帝，沛國譙人也。"
    b = "太祖武皇帝。沛國譙人也。"
    c = "太祖武皇帝，沛国谯人也。"  # simplified
    assert canonical_hash(a) != canonical_hash(b)
    assert canonical_hash(a) != canonical_hash(c)


# ---------- normalized_hash ----------

def test_normalized_hash_equates_traditional_and_simplified():
    trad = "太祖武皇帝，沛國譙人也。"
    simp = "太祖武皇帝，沛国谯人也。"
    assert normalized_hash(trad) == normalized_hash(simp)


def test_normalized_hash_ignores_punctuation_differences():
    a = "太祖武皇帝，沛國譙人也。"
    b = "太祖武皇帝沛國譙人也"
    c = "太祖武皇帝。沛國譙人也，"
    assert normalized_hash(a) == normalized_hash(b) == normalized_hash(c)


def test_normalized_hash_ignores_whitespace():
    assert normalized_hash("太祖 武 皇帝") == normalized_hash("太祖武皇帝")


def test_normalized_hash_distinguishes_real_textual_difference():
    a = "太祖武皇帝，沛國譙人也。"
    b = "太祖武皇帝，沛國譙縣人也。"  # 多一字
    assert normalized_hash(a) != normalized_hash(b)


# ---------- file_segments_sha256 ----------

def test_file_segments_sha256_invariant_to_input_order():
    s1 = Segment(id="wei.1.p1", text="太祖武皇帝。")
    s2 = Segment(id="wei.1.p2", text="桓帝世。")
    s3 = Segment(id="wei.1.p3", text="嵩生太祖。")
    assert file_segments_sha256([s1, s2, s3]) == file_segments_sha256([s3, s1, s2])


def test_file_segments_sha256_changes_when_a_segment_changes():
    s1 = Segment(id="wei.1.p1", text="太祖武皇帝。")
    s2 = Segment(id="wei.1.p2", text="桓帝世。")
    s2_alt = Segment(id="wei.1.p2", text="桓帝世也。")
    assert file_segments_sha256([s1, s2]) != file_segments_sha256([s1, s2_alt])


# ---------- parse_text ----------

SAMPLE = """---
work: sanguozhi
book: wei
juan: 1
title: 武帝紀
---

<a id="wei.1.p1"></a>
太祖武皇帝，沛國譙人也，姓曹，諱操。

<a id="wei.1.p2"></a>
桓帝世，曹騰為中常侍大長秋，
封費亭侯。
"""


def test_parse_text_extracts_frontmatter_and_segments():
    parsed = parse_text(SAMPLE)
    assert parsed.frontmatter["work"] == "sanguozhi"
    assert parsed.frontmatter["juan"] == 1
    assert [s.id for s in parsed.segments] == ["wei.1.p1", "wei.1.p2"]
    # Multi-line paragraph collapsed to single line (no internal newline).
    assert "\n" not in parsed.segments[1].text
    assert "桓帝世" in parsed.segments[1].text and "封費亭侯" in parsed.segments[1].text


def test_parse_text_rejects_missing_frontmatter():
    with pytest.raises(FormatError, match="frontmatter"):
        parse_text('<a id="wei.1.p1"></a>\n太祖。\n')


def test_parse_text_rejects_duplicate_segment_ids():
    bad = """---
work: sanguozhi
---

<a id="wei.1.p1"></a>
甲。

<a id="wei.1.p1"></a>
乙。
"""
    with pytest.raises(FormatError, match="duplicate"):
        parse_text(bad)


def test_parse_text_rejects_empty_segment():
    bad = """---
work: sanguozhi
---

<a id="wei.1.p1"></a>

<a id="wei.1.p2"></a>
乙。
"""
    with pytest.raises(FormatError, match="empty body"):
        parse_text(bad)


def test_parse_text_rejects_loose_text_between_anchors():
    bad = """---
work: sanguozhi
---

野生文字。

<a id="wei.1.p1"></a>
甲。
"""
    with pytest.raises(FormatError, match="outside a segment"):
        parse_text(bad)


# ---------- render_with_frontmatter round-trip ----------

def test_render_round_trip_preserves_segment_hashes():
    parsed = parse_text(SAMPLE)
    rendered = render_with_frontmatter(parsed.frontmatter, parsed.body)
    reparsed = parse_text(rendered)
    assert [(s.id, canonical_hash(s.text)) for s in parsed.segments] == \
           [(s.id, canonical_hash(s.text)) for s in reparsed.segments]
