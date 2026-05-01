"""Tests for tools/fetch_ctext.py — parser tested offline against an HTML fixture."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.fetch_ctext import (
    CtextAnnotation,
    parse_ctext_html,
    render_markdown,
)
from tools.segment import canonical_hash, parse_text

FIXTURE = Path(__file__).parent / "fixtures" / "ctext_sample.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# ---------- parse_ctext_html ----------

def test_parses_three_paragraphs_in_order(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    assert [p.para_no for p in chapter.paragraphs] == [1, 2, 3]
    assert [p.node_id for p in chapter.paragraphs] == ["100001", "100002", "100003"]


def test_paragraph_without_inlinecomment_keeps_full_text(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    p1 = chapter.paragraphs[0]
    assert p1.main_text == "太祖武皇帝，沛國譙人也，姓曹，諱操，字孟德。"
    assert p1.annotations == []


def test_inlinecomments_are_stripped_from_main_text(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    p2 = chapter.paragraphs[1]
    # 正文 = stuff outside <span class="inlinecomment">
    assert p2.main_text == "桓帝世，曹騰為中常侍大長秋。嵩生太祖。"
    # 王沈魏書曰 / 續漢書曰 must NOT appear in main_text
    assert "王沈魏書" not in p2.main_text
    assert "續漢書" not in p2.main_text


def test_annotations_anchor_at_correct_offsets(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    p2 = chapter.paragraphs[1]
    assert len(p2.annotations) == 2
    # First annotation comes before any 正文 → at == 0
    assert p2.annotations[0] == CtextAnnotation(at=0, text="王沈魏書曰：其先出於黃帝。")
    # Second annotation comes after "桓帝世，曹騰為中常侍大長秋。" (13 chars after whitespace strip)
    assert p2.annotations[1].at == len("桓帝世，曹騰為中常侍大長秋。")
    assert p2.annotations[1].text == "續漢書曰：嵩字巨高。"


def test_main_text_has_no_whitespace(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    for p in chapter.paragraphs:
        assert " " not in p.main_text
        assert "\n" not in p.main_text
        assert "\t" not in p.main_text


def test_rejects_wrong_ctext_juan(html):
    with pytest.raises(ValueError, match="expected"):
        parse_ctext_html(html, ctext_juan=99)


def test_raises_when_no_paragraphs():
    bad = "<html><body><p>nothing here</p></body></html>"
    with pytest.raises(ValueError, match="no paragraph numbers"):
        parse_ctext_html(bad, ctext_juan=1)


# ---------- render_markdown ----------

def test_render_markdown_produces_valid_file(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    md = render_markdown(
        chapter,
        work="sanguozhi",
        work_title="三國志",
        book="wei",
        book_title="魏書",
        work_prefix="wei",
        juan=1,
        title="武帝紀",
        author="陳壽",
        source_url="https://ctext.org/sanguozhi/1",
        source_sha256=hashlib.sha256(html.encode()).hexdigest(),
        source_retrieved="2026-05-01",
    )
    parsed = parse_text(md)
    # Frontmatter intact
    assert parsed.frontmatter["work"] == "sanguozhi"
    assert parsed.frontmatter["juan"] == 1
    assert parsed.frontmatter["title"] == "武帝紀"
    # All three paragraphs end up as segments with the right IDs.
    assert [s.id for s in parsed.segments] == ["wei.1.p1", "wei.1.p2", "wei.1.p3"]
    # segments_sha256 in frontmatter is consistent.
    from tools.segment import file_segments_sha256
    assert parsed.frontmatter["segments_sha256"] == file_segments_sha256(parsed.segments)


def test_render_markdown_segment_text_matches_canonical_hash(html):
    chapter = parse_ctext_html(html, ctext_juan=1)
    md = render_markdown(
        chapter,
        work="sanguozhi", work_title="三國志",
        book="wei", book_title="魏書", work_prefix="wei",
        juan=1, title="武帝紀", author="陳壽",
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    parsed = parse_text(md)
    p2 = next(s for s in parsed.segments if s.id == "wei.1.p2")
    assert canonical_hash(p2.text) == canonical_hash("桓帝世，曹騰為中常侍大長秋。嵩生太祖。")
