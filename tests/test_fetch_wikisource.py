"""Tests for tools/fetch_wikisource.py — parser tested offline against an HTML fixture."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.fetch_wikisource import (
    WSAnnotation,
    _ascii_encode_url,
    _normalize_title,
    _split_main_and_annotations,
    parse_wikisource_html,
    render_markdown,
)
from tools.segment import canonical_hash, file_segments_sha256, parse_text

FIXTURE = Path(__file__).parent / "fixtures" / "wikisource_sample.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# ---------- parse_wikisource_html ----------

def test_extracts_canonical_chapter_title(html):
    chapter = parse_wikisource_html(html)
    assert chapter.title == "魏書·后妃傳"


def test_para_no_is_document_order(html):
    chapter = parse_wikisource_html(html)
    assert [p.para_no for p in chapter.paragraphs] == [1, 2, 3]


def test_short_navigation_paragraphs_are_filtered(html):
    """The fixture has a <p>nav</p> and <p></p> that must NOT become segments."""
    chapter = parse_wikisource_html(html)
    assert len(chapter.paragraphs) == 3  # the three substantive ones
    for p in chapter.paragraphs:
        assert "nav" not in p.main_text


def test_paragraph_without_annotation_keeps_full_text(html):
    chapter = parse_wikisource_html(html)
    p1 = chapter.paragraphs[0]
    assert p1.main_text == "易稱「男正位乎外，女正位乎內」。古先哲王，莫不明后妃之制。"
    assert p1.annotations == []


def test_pei_annotation_is_stripped_and_anchored(html):
    chapter = parse_wikisource_html(html)
    p2 = chapter.paragraphs[1]
    assert p2.main_text == "武宣卞皇后，琅邪開陽人。本倡家，年二十，太祖納為妾。"
    assert p2.annotations == [
        WSAnnotation(at=len("武宣卞皇后，琅邪開陽人。本倡家，"), text="《魏書》曰：后以漢延熹三年生齊郡。"),
    ]


def test_multiple_annotations_in_one_paragraph(html):
    chapter = parse_wikisource_html(html)
    p3 = chapter.paragraphs[2]
    assert p3.main_text == "文昭甄皇后，中山無極人。后三歲失父。"
    assert len(p3.annotations) == 2
    # Both annotations attach right after "文昭甄皇后，中山無極人。" — same anchor
    anchor = len("文昭甄皇后，中山無極人。")
    assert all(a.at == anchor for a in p3.annotations)
    assert p3.annotations[0].text == "《魏書》曰：甄逸娶常山張氏。"
    assert p3.annotations[1].text == "《魏略》曰：后以漢光和五年生。"


def test_main_text_has_no_whitespace(html):
    chapter = parse_wikisource_html(html)
    for p in chapter.paragraphs:
        assert " " not in p.main_text and "\n" not in p.main_text and "\t" not in p.main_text


def test_raises_when_no_paragraphs():
    with pytest.raises(ValueError, match="no body paragraphs"):
        parse_wikisource_html("<html><body><div class='mw-parser-output'></div></body></html>")


def test_nested_brackets_are_handled_as_part_of_outer_annotation():
    """Cross-references like 〈X〉 inside 〈裴注〉 stay inside the annotation."""
    html = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />蜀書·後主傳</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p>後主即位。〈《魏略》曰：見〈二主妃子傳〉與〈後主傳〉所載。〉次年改元。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(html)
    p = chapter.paragraphs[0]
    assert p.main_text == "後主即位。次年改元。"
    assert len(p.annotations) == 1
    assert p.annotations[0].text == "《魏略》曰：見〈二主妃子傳〉與〈後主傳〉所載。"
    assert p.annotations[0].at == len("後主即位。")


def test_unbalanced_brackets_fall_back_to_lenient():
    """If brackets don't balance across the whole body, parse with lenient regex strip
    and record a warning. The orphan 〈 stays in the canonical text rather than blocking
    ingestion of the chapter."""
    bad = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />魏書·武帝紀</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p>第一段〈被剝離的注一〉真正文。</p>'
        '<p>第二段有不匹配的〈開括號但沒有閉合，數據缺失。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(bad)
    assert len(chapter.parse_warnings) == 1
    assert "lenient" in chapter.parse_warnings[0]
    # Para 1: balanced, lenient stripping is fine.
    assert chapter.paragraphs[0].main_text == "第一段真正文。"
    # Para 2: orphan 〈 stays in the canonical (flagged for manual review).
    assert "〈" in chapter.paragraphs[1].main_text
    # Lenient mode does not extract annotations.
    assert all(p.annotations == [] for p in chapter.paragraphs)


def test_strict_unbalanced_close_raises():
    """The strict per-paragraph helper still raises for spot-check tests."""
    from tools.fetch_wikisource import _split_main_and_annotations
    with pytest.raises(ValueError, match="unbalanced"):
        _split_main_and_annotations("正文〉沒有對應的開括號")


def test_split_main_and_annotations_unit():
    main, anns = _split_main_and_annotations("正文〈A〈B〉C〉接續〈D〉尾")
    assert main == "正文接續尾"
    assert [(a.at, a.text) for a in anns] == [
        (len("正文"), "A〈B〉C"),
        (len("正文接續"), "D"),
    ]


def test_split_main_when_paragraph_is_pure_annotation():
    main, anns = _split_main_and_annotations("〈整段都是裴注〉")
    assert main == ""
    assert anns[0].text == "整段都是裴注"


# ---------- title normalization ----------

@pytest.mark.parametrize("raw,expected", [
    ("魏書·后妃傳", "魏書·后妃傳"),
    ("《吳書》·妃嬪傳", "吳書·妃嬪傳"),
    ("蜀書四 二主妃子傳", "蜀書·二主妃子傳"),
    ("蜀書五 諸葛亮傳", "蜀書·諸葛亮傳"),
    ("吳書一 孫破虜討逆傳", "吳書·孫破虜討逆傳"),
    # Bullet U+2022 instead of middle-dot
    ("魏書•武帝紀", "魏書·武帝紀"),
    # Japanese kanji for 吳
    ("呉書·孫破虜討逆傳", "吳書·孫破虜討逆傳"),
    # Simplified 吴
    ("吴書·陆逊传", "吳書·陆逊传"),
    # Ideographic full-width space U+3000 separator
    ("吳書　三嗣主傳第三", "吳書·三嗣主傳"),
    # Trailing 第N suffix
    ("魏書三十 烏丸鮮卑東夷傳第三十", "魏書·烏丸鮮卑東夷傳"),
    # Leading 卷N prefix (with space or middle-dot terminator)
    ("卷四十九 吳書四 劉繇太史慈士燮傳", "吳書·劉繇太史慈士燮傳"),
    ("卷五十五·吳書·十二虎臣傳 第十", "吳書·十二虎臣傳"),
    # Unknown format falls back to raw
    ("無法識別的格式", "無法識別的格式"),
])
def test_normalize_title(raw, expected):
    assert _normalize_title(raw) == expected


def test_wikilinks_are_stripped_keeping_inner_text():
    html = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />魏書·武帝紀</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p><a href="/wiki/曹操" title="曹操">曹操</a>，沛國譙人也，姓曹諱操字孟德。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(html)
    assert chapter.paragraphs[0].main_text == "曹操，沛國譙人也，姓曹諱操字孟德。"


# ---------- render_markdown ----------

def test_render_markdown_produces_valid_file(html):
    chapter = parse_wikisource_html(html)
    md = render_markdown(
        chapter,
        work="sanguozhi", work_title="三國志",
        book="wei", book_title="魏書", work_prefix="wei",
        juan=5, title=chapter.title, author="陳壽",
        source_url="https://zh.wikisource.org/wiki/三國志/卷05",
        source_sha256=hashlib.sha256(html.encode()).hexdigest(),
        source_retrieved="2026-05-01",
    )
    parsed = parse_text(md)
    assert parsed.frontmatter["source"]["id"] == "wikisource"
    assert parsed.frontmatter["title"] == "魏書·后妃傳"
    assert [s.id for s in parsed.segments] == ["wei.5.p1", "wei.5.p2", "wei.5.p3"]
    assert parsed.frontmatter["segments_sha256"] == file_segments_sha256(parsed.segments)


# ---------- _ascii_encode_url ----------

def test_ascii_encode_url_percent_encodes_chinese_path():
    raw = "https://zh.wikisource.org/wiki/三國志/卷01"
    encoded = _ascii_encode_url(raw)
    assert encoded.encode("ascii")  # round-trips through ASCII
    assert "三國志" not in encoded
    assert "%E4%B8%89%E5%9C%8B%E5%BF%97" in encoded  # 三國志 in UTF-8 percent form


def test_ascii_encode_url_idempotent_on_already_encoded():
    raw = "https://zh.wikisource.org/wiki/%E4%B8%89%E5%9C%8B%E5%BF%97/%E5%8D%B701"
    assert _ascii_encode_url(raw) == raw


def test_render_segment_text_matches_canonical_hash(html):
    chapter = parse_wikisource_html(html)
    md = render_markdown(
        chapter,
        work="sanguozhi", work_title="三國志",
        book="wei", book_title="魏書", work_prefix="wei",
        juan=5, title=chapter.title, author="陳壽",
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    parsed = parse_text(md)
    p2 = next(s for s in parsed.segments if s.id == "wei.5.p2")
    assert canonical_hash(p2.text) == canonical_hash("武宣卞皇后，琅邪開陽人。本倡家，年二十，太祖納為妾。")
