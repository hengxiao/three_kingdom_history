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


def test_orphan_bracket_kept_as_literal_balanced_annotations_still_extracted():
    """Stray '〈' in the source becomes literal text; annotations from balanced
    pairs around it are still extracted (much better than dropping the whole chapter)."""
    bad = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />魏書·武帝紀</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p>第一段〈被剝離的注一〉真正文。</p>'
        '<p>第二段有不匹配的〈開括號但沒有閉合，數據缺失再多幾字湊夠最少漢字。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(bad)
    assert len(chapter.parse_warnings) == 1
    assert "stray '〈'" in chapter.parse_warnings[0]
    # Para 1: balanced annotation extracted normally.
    assert chapter.paragraphs[0].main_text == "第一段真正文。"
    assert len(chapter.paragraphs[0].annotations) == 1
    assert chapter.paragraphs[0].annotations[0].text == "被剝離的注一"
    # Para 2: orphan 〈 stays in the canonical text; no annotation, no exception.
    assert "〈" in chapter.paragraphs[1].main_text
    assert chapter.paragraphs[1].annotations == []


def test_orphan_close_bracket_kept_as_literal():
    """A stray '〉' is treated as literal too."""
    bad = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />魏書·武帝紀</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p>第一段〈正常注〉常文。</p>'
        '<p>第二段有多餘的〉閉括號，數據缺失再多幾字湊夠最少漢字。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(bad)
    assert any("stray '〉'" in w for w in chapter.parse_warnings)
    # First annotation still extracts.
    assert chapter.paragraphs[0].annotations[0].text == "正常注"
    # Stray 〉 stays in canonical of the second paragraph.
    assert "〉" in chapter.paragraphs[1].main_text


def test_houhanshu_bracketed_marker_convention_extracts_annotations():
    """Some 后汉书 chapters mark 李贤注 with [一] in canonical + 注[一]<text>; ensure they parse."""
    html = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>後漢書</a></b><br />虞傅蓋臧列傳 第四十八</td></tr></table>'
        '<div class="mw-content-ltr mw-parser-output">'
        '<p>虞詡字升卿，陳國武平人也。[一]祖父經，為郡縣獄吏。[二]決獄六十年矣。</p>'
        '<p>注[一]武平故城在今亳州鹿邑縣東北。</p>'
        '<p>注[二]前書，于定國字曼倩，東海人。其父于公為縣獄吏。</p>'
        '</div>'
    )
    chapter = parse_wikisource_html(html, work="houhanshu")
    # The first paragraph should have the canonical text (no [一] markers in it) and
    # two annotations attached at the original marker positions.
    p = chapter.paragraphs[0]
    assert p.main_text == "虞詡字升卿，陳國武平人也。祖父經，為郡縣獄吏。決獄六十年矣。"
    assert len(p.annotations) == 2
    assert p.annotations[0].text == "武平故城在今亳州鹿邑縣東北。"
    assert p.annotations[0].at == len("虞詡字升卿，陳國武平人也。")
    assert p.annotations[1].text == "前書，于定國字曼倩，東海人。其父于公為縣獄吏。"
    assert p.annotations[1].at == len("虞詡字升卿，陳國武平人也。祖父經，為郡縣獄吏。")


def test_houhanshu_brackets_do_not_match_sanguozhi_chapters():
    """Sanguozhi pages still use 〈...〉 — make sure the hhs heuristic doesn't hijack them."""
    chapter = parse_wikisource_html(FIXTURE.read_text(encoding="utf-8"), work="sanguozhi")
    # original sample has 3 paragraphs — verifying we still get them via the 〈〉 pipeline
    assert len(chapter.paragraphs) == 3


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
    ("卷八·帝紀第八　孝靈皇帝", "孝靈皇帝"),
    ("卷九·帝紀第九 孝獻帝", "孝獻帝"),
    ("皇后紀第十下", "皇后紀"),
    ("馬融列傳 第五十上", "馬融列傳"),
    ("蔡邕列傳 第五十下", "蔡邕列傳"),
    ("董卓列傳 第六十二", "董卓列傳"),
    ("袁紹劉表列傳　第六十四下", "袁紹劉表列傳"),
    ("卷七十 鄭孔荀列傳 第六十", "鄭孔荀列傳"),
    ("卷八十下·文苑列傳第七十下", "文苑列傳"),
    ("卷七十九上·儒林列传第六十九上", "儒林列传"),
])
def test_normalize_title_hhs(raw, expected):
    from tools.fetch_wikisource import _normalize_title_hhs
    assert _normalize_title_hhs(raw) == expected


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
