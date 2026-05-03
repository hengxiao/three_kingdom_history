"""Tests for tools/dates_resolver.py — number parsing, era resolution, date extraction."""
from __future__ import annotations

import pytest

from tools.dates_resolver import (
    TimelineState,
    chinese_to_int,
    chinese_to_month,
    find_dates,
    resolve_era,
    resolve_segment,
    to_ad,
)


# ---------- chinese_to_int ----------

@pytest.mark.parametrize("inp,expected", [
    ("元", 1),
    ("一", 1),
    ("二", 2),
    ("九", 9),
    ("十", 10),
    ("十一", 11),
    ("十九", 19),
    ("二十", 20),
    ("二十一", 21),
    ("二十四", 24),
    ("二十五", 25),
    ("三十", 30),
    ("九十九", 99),
])
def test_chinese_to_int_recognized(inp, expected):
    assert chinese_to_int(inp) == expected


@pytest.mark.parametrize("inp", ["", "甲", "百", "二十二十", "十十", "千一", "一二"])
def test_chinese_to_int_unrecognized(inp):
    assert chinese_to_int(inp) is None


# ---------- chinese_to_month ----------

def test_zheng_means_first_month():
    assert chinese_to_month("正") == 1


@pytest.mark.parametrize("m,expected", [
    ("一", 1), ("二", 2), ("十", 10), ("十一", 11), ("十二", 12),
])
def test_month_numerals(m, expected):
    assert chinese_to_month(m) == expected


def test_month_above_twelve_is_invalid():
    assert chinese_to_month("十三") is None


# ---------- resolve_era / to_ad ----------

def test_jianan_5_resolves_to_ad_200_in_wei_book():
    era = resolve_era("建安", 5, book="wei")
    assert era is not None and era.dynasty == "han"
    assert to_ad(era, 5) == 200


def test_huangchu_yuan_year_resolves_to_220():
    era = resolve_era("黃初", 1, book="wei")
    assert era is not None
    assert to_ad(era, 1) == 220


def test_jianxing_disambiguates_by_book():
    """建興 is a homonym in shu (223–237) and wu (252–253)."""
    shu = resolve_era("建興", 5, book="shu")
    wu = resolve_era("建興", 1, book="wu")
    assert shu is not None and shu.dynasty == "shu"
    assert to_ad(shu, 5) == 227
    assert wu is not None and wu.dynasty == "wu"
    assert to_ad(wu, 1) == 252


def test_jianxing_with_year_outside_wu_range_resolves_to_shu():
    """建興十年 cannot be 吳 (only 2 years long), so it must be 蜀."""
    era = resolve_era("建興", 10, book="shu")
    assert era is not None and era.dynasty == "shu"
    assert to_ad(era, 10) == 232


def test_unknown_era_returns_none():
    assert resolve_era("不存在的年號", 1, book="wei") is None


def test_year_zero_or_negative_rejected():
    assert resolve_era("建安", 0, book="wei") is None
    assert resolve_era("建安", -1, book="wei") is None


def test_era_year_past_range_rejected():
    """建安 ran 25 years (196–220); year 50 is impossible."""
    assert resolve_era("建安", 50, book="wei") is None


# ---------- find_dates ----------

def test_finds_simple_era_year():
    text = "建安五年春正月，太祖討袁紹"
    dates = find_dates(text, book="wei")
    assert len(dates) == 1
    d = dates[0]
    assert d.surface == "建安五年春正月"
    assert d.era.name == "建安"
    assert d.era_year == 5
    assert d.year_ad == 200
    assert d.month_chinese == "春正月"
    assert d.month_ordinal == 1
    assert d.at == 0
    assert d.length == 7


def test_finds_year_without_month():
    dates = find_dates("建安二十四年，太祖薨于洛陽。", book="wei")
    assert len(dates) == 1
    assert dates[0].surface == "建安二十四年"
    assert dates[0].year_ad == 219
    assert dates[0].month_chinese is None


def test_yuan_year_resolves_to_one():
    dates = find_dates("黃初元年冬十月", book="wei")
    assert len(dates) == 1
    assert dates[0].era_year == 1
    assert dates[0].year_ad == 220
    assert dates[0].month_chinese == "冬十月"
    assert dates[0].month_ordinal == 10


def test_finds_multiple_dates_in_one_paragraph():
    """Season alone (e.g. just '春' after the year) is not part of the surface —
    month capture requires both season-or-nothing AND a digit + 月."""
    text = "建安五年，太祖大敗紹於官渡。建安十二年春正月，太祖還鄴。"
    dates = find_dates(text, book="wei")
    assert [d.surface for d in dates] == ["建安五年", "建安十二年春正月"]
    assert dates[0].year_ad == 200
    assert dates[1].year_ad == 207
    assert dates[1].month_ordinal == 1


def test_bare_season_without_month_is_not_appended():
    text = "建安十二年春，太祖征烏丸。"
    dates = find_dates(text, book="wei")
    assert dates[0].surface == "建安十二年"
    assert dates[0].month_chinese is None


def test_dynasty_prefix_kept_in_surface_but_doesnt_block_resolution():
    """裴注 sometimes writes 漢延熹三年 to be explicit about the dynasty."""
    dates = find_dates("漢延熹三年生，後嫁太祖。", book="wei")
    assert len(dates) == 1
    d = dates[0]
    assert d.surface == "漢延熹三年"
    assert d.era.name == "延熹"
    assert d.year_ad == 160


def test_skips_unknown_era_in_text():
    """A reign-era-like substring whose era we don't know should be ignored, not raise."""
    dates = find_dates("永光二年事", book="wei")  # 永光 is Han Yuan-Di era (not in our table)
    assert dates == []


def test_skips_year_out_of_range():
    """興平 ran only 2 years; 興平十年 is bogus and should not be emitted."""
    dates = find_dates("興平十年正月", book="wei")
    assert dates == []


def test_at_position_is_within_unicode_string():
    """The `at` index is into the Python str (chars), not bytes."""
    text = "正文一些字。建安五年，又有事。"
    dates = find_dates(text, book="wei")
    assert dates[0].at == text.index("建安五年")
    # Verify slice round-trip: text[at:at+length] == surface.
    d = dates[0]
    assert text[d.at:d.at + d.length] == d.surface


def test_jianxing_in_shu_book_picks_shu_era():
    dates = find_dates("建興元年夏五月", book="shu")
    assert len(dates) == 1
    assert dates[0].era.dynasty == "shu"
    assert dates[0].year_ad == 223


def test_jianxing_in_wu_book_picks_wu_era():
    dates = find_dates("建興元年", book="wu")
    assert len(dates) == 1
    assert dates[0].era.dynasty == "wu"
    assert dates[0].year_ad == 252


# ---------- post-光武 mid-late 漢 eras (added for 后汉书 coverage) ----------

@pytest.mark.parametrize("era_name,era_year,expected_ad", [
    ("建武", 1, 25),       # 光武帝即位
    ("永平", 1, 58),       # 明帝
    ("永和", 1, 136),      # 順帝
    ("建和", 1, 147),      # 桓帝
    ("永興", 1, 153),      # 桓帝（漢, 与西晉 304 同名 — for hhs picks han）
    ("永壽", 4, 158),      # 桓帝末年
    ("漢安", 2, 143),
])
def test_post_guangwu_han_eras_resolve_in_hhs(era_name, era_year, expected_ad):
    era = resolve_era(era_name, era_year, book="hhs")
    assert era is not None and era.dynasty == "han"
    assert to_ad(era, era_year) == expected_ad


def test_backward_absolute_ref_is_flashback_does_not_move_state():
    """In 编年体 + bio-style works, an absolute ref to an earlier year is a flashback —
    emit it correctly, but don't drag subsequent bare-month/year refs back with it."""
    state = TimelineState()
    # State opens at AD 227 via h3-derived header.
    _, state = resolve_segment("太和元年", book="zztj", state=state)
    assert state.year_ad == 227
    # Body paragraph references 建安二十四年 (AD 219) as flashback.
    matches, state2 = resolve_segment("初，建安二十四年，太祖薨于洛陽。其後事多……", book="zztj", state=state)
    assert any(m.year_ad == 219 for m in matches)  # the flashback annotation IS emitted
    assert state2.year_ad == 227                    # but state stays at 227
    # Subsequent 二月 inherits 227, NOT 219.
    matches3, _ = resolve_segment("二月，吳使來。", book="zztj", state=state2)
    assert matches3[0].year_ad == 227 and matches3[0].month_ordinal == 2


def test_yongxi_alt_form_resolves_too():
    """沖帝在位的 145 年，史書同時有 永熹/永憙 兩寫法。"""
    for name in ("永熹", "永憙"):
        era = resolve_era(name, 1, book="hhs")
        assert era is not None
        assert to_ad(era, 1) == 145


# ---------- resolve_segment: state-aware (phase 2) ----------

def test_resolve_segment_handles_absolute_only():
    dates, state = resolve_segment("建安五年春正月，太祖戰。", book="wei")
    assert len(dates) == 1
    assert dates[0].kind == "absolute"
    assert dates[0].year_ad == 200
    assert state.era.name == "建安"
    assert state.era_year == 5
    assert state.year_ad == 200


def test_resolve_segment_resolves_bare_year_using_state():
    state = TimelineState()
    dates, state = resolve_segment("建安五年，太祖戰。", book="wei", state=state)
    # Now state = (建安, 5, 200). Next segment uses bare-year ref:
    dates2, state2 = resolve_segment("九年春正月，攻鄴。", book="wei", state=state)
    assert len(dates2) == 1
    d = dates2[0]
    assert d.kind == "relative"
    assert d.resolution == "bare_year"
    assert d.year_ad == 204
    assert d.era.name == "建安"
    assert d.era_year == 9
    assert d.month_ordinal == 1
    assert state2.year_ad == 204


def test_resolve_segment_resolves_bare_month():
    _, state = resolve_segment("建安五年春正月，太祖戰。", book="wei")
    dates, _ = resolve_segment("二月，紹潰。", book="wei", state=state)
    assert len(dates) == 1
    d = dates[0]
    assert d.resolution == "bare_month"
    assert d.year_ad == 200
    assert d.month_ordinal == 2


def test_resolve_segment_handles_shi_sui():
    _, state = resolve_segment("建安五年，太祖戰。", book="wei")
    dates, _ = resolve_segment("是歲，孫策卒。", book="wei", state=state)
    assert len(dates) == 1
    assert dates[0].resolution == "this_year"
    assert dates[0].year_ad == 200


def test_resolve_segment_handles_ming_nian_advances_state():
    _, state = resolve_segment("中平六年，靈帝崩。", book="wei")
    dates, state2 = resolve_segment("明年春正月，諸將起兵。", book="wei", state=state)
    assert len(dates) == 1
    d = dates[0]
    assert d.resolution == "next_year"
    assert d.year_ad == 190  # 中平 ended in 189; AD+1 crosses to next era
    assert d.era is None     # crossed boundary, era unknown
    assert state2.year_ad == 190


def test_resolve_segment_handles_qu_nian_does_not_move_state():
    _, state = resolve_segment("建安五年，太祖戰。", book="wei")
    dates, state2 = resolve_segment("去年事尚未息。", book="wei", state=state)
    assert len(dates) == 1
    assert dates[0].resolution == "prev_year"
    assert dates[0].year_ad == 199
    # state not advanced — subsequent refs still resolve from year 200
    assert state2.year_ad == 200


def test_resolve_segment_drops_bare_year_before_any_anchor():
    """Without a prior absolute anchor we cannot resolve `三年春正月`."""
    dates, state = resolve_segment("三年春正月，事起。", book="wei")
    assert dates == []
    assert state.year_ad is None


def test_resolve_segment_drops_bare_year_outside_era_range():
    _, state = resolve_segment("興平元年，事起。", book="wei")  # 興平 has 2 years
    dates, _ = resolve_segment("十年春正月，又有事。", book="wei", state=state)
    # 興平 doesn't have a year 10 — drop the ref
    assert all(d.resolution != "bare_year" for d in dates)


def test_resolve_segment_does_not_double_count_year_with_attached_month():
    """`建安五年春正月` should be ONE absolute match, not absolute + bare_year + bare_month."""
    dates, _ = resolve_segment("建安五年春正月，太祖戰。", book="wei")
    assert len(dates) == 1
    assert dates[0].kind == "absolute"


def test_resolve_segment_bare_year_with_attached_month():
    _, state = resolve_segment("建安五年，事起。", book="wei")
    dates, state2 = resolve_segment("九年春正月，事興。", book="wei", state=state)
    assert len(dates) == 1
    d = dates[0]
    assert d.surface == "九年春正月"
    assert d.year_ad == 204
    assert d.month_ordinal == 1


def test_resolve_segment_state_carries_across_many_segments():
    """Walk a sequence of segments, verify the state machine accumulates correctly."""
    state = TimelineState()
    dates, state = resolve_segment("建安五年春正月，討袁紹。", book="wei", state=state)
    assert dates[0].year_ad == 200
    dates, state = resolve_segment("二月，紹敗。", book="wei", state=state)
    assert dates[0].year_ad == 200
    assert dates[0].month_ordinal == 2
    dates, state = resolve_segment("六年，紹卒。", book="wei", state=state)  # 建安六年
    assert dates[0].year_ad == 201
    dates, state = resolve_segment("是歲，蜀大水。", book="wei", state=state)
    assert dates[0].year_ad == 201
    dates, state = resolve_segment("明年，太祖征荊州。", book="wei", state=state)
    assert dates[0].year_ad == 202
    assert dates[0].era.name == "建安"  # still inside 建安 era
    assert dates[0].era_year == 7
