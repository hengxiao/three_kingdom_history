"""Tests for tools/dates_resolver.py — number parsing, era resolution, date extraction."""
from __future__ import annotations

import pytest

from tools.dates_resolver import (
    chinese_to_int,
    chinese_to_month,
    find_dates,
    resolve_era,
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
