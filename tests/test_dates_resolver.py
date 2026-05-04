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


def test_duration_phrase_not_caught_as_bare_year():
    """`連戰二年` (fought for 2 years) is a duration mid-sentence, not the second
    year of the current era. The bare-year sentence-boundary filter must drop it
    so the next paragraph's 是歲 stays anchored at the actual current year."""
    state = TimelineState()
    # Set state to 初平四年 (AD 193).
    _, state = resolve_segment("初平四年", book="hhs", state=state)
    assert state.year_ad == 193
    # A body paragraph with a duration phrase MUST NOT advance state backward.
    matches, state2 = resolve_segment(
        "乘勝而南，攻下郡縣，紹復遣兵數萬與揩連戰二年，糧食並盡。",
        book="hhs", state=state,
    )
    assert all(m.resolution != "bare_year" for m in matches), \
        "「連戰二年」 should not be tagged bare_year"
    assert state2.year_ad == 193  # 是歲 in the next paragraph would resolve correctly
    # Subsequent 是歲 still resolves to 193, not 191.
    matches3, _ = resolve_segment("是歲，瓚破禽劉虞。", book="hhs", state=state2)
    assert matches3[0].resolution == "this_year"
    assert matches3[0].year_ad == 193


def test_bare_year_at_segment_start_still_resolves():
    """Sentence-boundary filter must not block legitimate refs at segment start."""
    _, state = resolve_segment("初平元年春正月", book="hhs")
    matches, _ = resolve_segment("二年春正月，紹敗。", book="hhs", state=state)
    assert any(m.resolution == "bare_year" and m.year_ad == 191 for m in matches)


def test_bare_year_after_period_still_resolves():
    """Bare year at start of a new sentence (after 。) is a legitimate date ref."""
    _, state = resolve_segment("初平元年", book="hhs")
    matches, _ = resolve_segment("前事既畢。三年，攻鄴。", book="hhs", state=state)
    bare = [m for m in matches if m.resolution == "bare_year"]
    assert len(bare) == 1 and bare[0].year_ad == 192


def test_reasoning_populated_for_each_resolution_kind():
    """Every emitted DateMatch carries a `reasoning` string for downstream review."""
    _, state = resolve_segment("建安五年", book="wei")
    # bare_year
    m1, state = resolve_segment("六年，攻鄴。", book="wei", state=state)
    assert m1[0].reasoning and "建安" in m1[0].reasoning and "AD 201" in m1[0].reasoning
    # bare_month
    m2, state = resolve_segment("夏五月，伐袁。", book="wei", state=state)
    assert m2[0].reasoning and "承前文" in m2[0].reasoning
    # this_year
    m3, _ = resolve_segment("是歲，孫策卒。", book="wei", state=state)
    assert m3[0].reasoning and "当前年" in m3[0].reasoning
    # next_year
    m4, _ = resolve_segment("明年，征荊州。", book="wei", state=state)
    assert m4[0].reasoning and "次年" in m4[0].reasoning
    # absolute (and reasoning explains it directly)
    m5, _ = resolve_segment("黃初元年，禪位。", book="wei", state=state)
    assert m5[0].reasoning and "直接出现" in m5[0].reasoning


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


# ---------- F3: duration-month false positives ----------

@pytest.mark.parametrize("text,expect_dates", [
    # 「輒一月寒食」 — yearly mid-winter custom of one-month cold-food fasting; 一月 is duration
    ("每冬中輒一月寒食。", 0),
    # 「凡六月日」 — siege lasted six months; 六月 is duration with 日 suffix
    ("魏攻圍然凡六月日，未退。", 0),
    # 「凡九月焉」 — exile-to-return totaled nine months
    ("自徙及歸，凡九月焉。", 0),
    # 「可一月」 — approximately one month
    ("其實可一月，事乃成。", 0),
    # 「經三月」 — passed three months
    ("經三月不出。", 0),
    # 「歷十年」 — over ten years (caught by sentence-boundary on bare_year, but covered for safety)
    ("歷十月乃定。", 0),
    # Sanity: 「凡二月、五月產子」 — list of taboo months; 凡 + N月 NOT followed by duration
    # suffix should still be treated as month references (here at sentence start)
    ("凡二月產子。", 1),
    # 「圍然凡六月」 — siege of six months; verb 「圍」 + particle 「然」 before 凡 = duration
    ("魏兵圍然凡六月，未退。", 0),
    # 「被攻凡六月」 — attacked for six months; passive 「被攻」 before 凡 = duration
    ("羅憲被攻凡六月，救援不到。", 0),
])
def test_resolve_segment_duration_month_filtered(text, expect_dates):
    # No state — these should fail purely on duration filter / sentence-boundary
    # We pre-seed state so bare_month has a year to anchor on.
    state = TimelineState()
    dates, state = resolve_segment("永和四年。", book="hhs", state=state)  # set state to AD139
    assert dates[0].year_ad == 139
    dates, _ = resolve_segment(text, book="hhs", state=state)
    assert len(dates) == expect_dates, f"{text!r} → {[d.surface for d in dates]}"


# ---------- F2: same-segment absolute anchors subsequent relatives ----------

def test_resolve_segment_backward_absolute_anchors_same_segment_relative():
    """The user-flagged hhs.61.p28 case: when a backward absolute appears in the
    same segment as 是歲, 是歲 should resolve to the LOCAL absolute, not chapter state."""
    state = TimelineState()
    # Set chapter state to 永和四年 (AD139) via prior segment.
    dates, state = resolve_segment("永和四年。", book="hhs", state=state)
    assert dates[0].year_ad == 139
    # Now a segment that opens with a BACKWARD absolute (陽嘉三年 = AD134)
    # followed by 是歲. Old behavior: 是歲 → 139. New behavior: 是歲 → 134.
    text = "陽嘉三年，左雄薦舉，征拜尚書。是歲，河南、三輔大旱。"
    dates, state2 = resolve_segment(text, book="hhs", state=state)
    by_surface = {d.surface: d for d in dates}
    assert by_surface["陽嘉三年"].year_ad == 134
    assert "回溯前事" in by_surface["陽嘉三年"].reasoning
    assert by_surface["是歲"].year_ad == 134, "F2: 是歲 anchors on local 陽嘉三年, not stale state"
    # Forward state for the NEXT segment must NOT be polluted by the backward absolute.
    assert state2.year_ad == 139, "F2: forward state preserved across flashback segments"


def test_resolve_segment_local_state_advances_only_locally():
    """Inside a flashback regime, 明年 advances local_state but not forward state."""
    state = TimelineState()
    dates, state = resolve_segment("永和四年。", book="hhs", state=state)
    assert state.year_ad == 139
    # Backward absolute then 明年 — 明年 should be backward+1, forward state unchanged.
    text = "陽嘉三年，事起。明年，事繼。"
    dates, state2 = resolve_segment(text, book="hhs", state=state)
    by_surface = {d.surface: d for d in dates}
    assert by_surface["陽嘉三年"].year_ad == 134
    assert by_surface["明年"].year_ad == 135, "明年 anchors on 陽嘉三年 (backward), not on state"
    assert state2.year_ad == 139, "forward state never sees the flashback advance"


def test_resolve_segment_forward_absolute_clears_flashback_regime():
    """A forward absolute mid-segment exits flashback mode and resumes normal advance."""
    state = TimelineState()
    dates, state = resolve_segment("永和四年。", book="hhs", state=state)
    text = "陽嘉三年，事起。永和五年，事終。是歲，又有大旱。"
    dates, state2 = resolve_segment(text, book="hhs", state=state)
    by_surface = {d.surface: d for d in dates}
    assert by_surface["陽嘉三年"].year_ad == 134
    assert by_surface["永和五年"].year_ad == 140
    assert by_surface["是歲"].year_ad == 140, "是歲 after forward absolute anchors on the new forward year"
    assert state2.year_ad == 140


def test_resolve_segment_forward_absolute_no_flashback_unchanged():
    """Sanity: when no flashback fires, behavior matches the pre-F2 code path."""
    state = TimelineState()
    dates, state = resolve_segment("陽嘉元年。", book="hhs", state=state)
    text = "明年，事起。是歲，雨。"
    dates, _ = resolve_segment(text, book="hhs", state=state)
    by_surface = {d.surface: d for d in dates}
    assert by_surface["明年"].year_ad == 133
    assert by_surface["是歲"].year_ad == 133
