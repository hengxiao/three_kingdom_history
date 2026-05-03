"""Resolve Chinese reign-era dates to Gregorian AD years for the 三国志 corpus.

Era table covers 漢末 + 魏 + 蜀 + 吳 (~AD 158–280). A handful of names recur
across dynasties (建興 in 蜀 and 吳; 甘露 in 魏 and 吳); we disambiguate using
the book the date appears in (魏書 → 魏+漢, 蜀書 → 蜀+漢, 吳書 → 吳+漢).

Date surface forms recognized:
    建安五年                       — era + year only
    建安五年春正月                 — era + year + month (with optional season)
    黃初元年                       — uses 元 for year 1
    漢延熹三年, 魏黃初元年         — optional dynasty prefix (kept in `surface`,
                                     ignored for resolution since the era
                                     itself is unambiguous in our data set)

Day-of-month and sexagenary (干支) suffixes are not captured — out of scope
for the first pass; downstream tooling can handle them later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Era:
    name: str
    start_ad: int   # AD year of era-year 1
    end_ad: int     # AD year of last era-year (inclusive)
    dynasty: str    # one of: han | wei | shu | wu


# Curated from standard 中國紀年 reference tables. Order is chronological per dynasty.
ERAS: tuple[Era, ...] = (
    # 後漢初~中（光武 → 桓帝早期）— mostly referenced from 后汉书 列傳 and 三国志 裴注
    Era("更始", 23, 25, "han"),     # 更始帝 劉玄
    Era("建武", 25, 56, "han"),     # 光武帝
    Era("中元", 56, 57, "han"),     # 光武帝（亦稱建武中元）
    Era("永平", 58, 75, "han"),     # 明帝
    Era("建初", 76, 84, "han"),     # 章帝
    Era("元和", 84, 87, "han"),     # 章帝
    Era("章和", 87, 88, "han"),     # 章帝
    Era("永元", 89, 105, "han"),    # 和帝
    Era("元興", 105, 105, "han"),   # 和帝
    Era("延平", 106, 106, "han"),   # 殤帝
    Era("永初", 107, 113, "han"),   # 安帝
    Era("元初", 114, 120, "han"),   # 安帝
    Era("永寧", 120, 121, "han"),   # 安帝
    Era("建光", 121, 122, "han"),   # 安帝
    Era("延光", 122, 125, "han"),   # 安帝
    Era("永建", 126, 132, "han"),   # 順帝
    Era("陽嘉", 132, 135, "han"),   # 順帝
    Era("永和", 136, 141, "han"),   # 順帝
    Era("漢安", 142, 144, "han"),   # 順帝
    Era("建康", 144, 144, "han"),   # 順帝
    Era("永熹", 145, 145, "han"),   # 沖帝（亦作 永憙）
    Era("永憙", 145, 145, "han"),   # 沖帝（同上，異體）
    Era("本初", 146, 146, "han"),   # 質帝
    Era("建和", 147, 149, "han"),   # 桓帝
    Era("和平", 150, 150, "han"),   # 桓帝
    Era("元嘉", 151, 152, "han"),   # 桓帝
    Era("永興", 153, 154, "han"),   # 桓帝
    Era("永壽", 155, 158, "han"),   # 桓帝
    # 漢末
    Era("延熹", 158, 167, "han"),
    Era("永康", 167, 167, "han"),
    Era("建寧", 168, 172, "han"),
    Era("熹平", 172, 178, "han"),
    Era("光和", 178, 184, "han"),
    Era("中平", 184, 189, "han"),
    Era("光熹", 189, 189, "han"),
    Era("昭寧", 189, 189, "han"),
    Era("永漢", 189, 189, "han"),
    Era("初平", 190, 193, "han"),
    Era("興平", 194, 195, "han"),
    Era("建安", 196, 220, "han"),
    Era("延康", 220, 220, "han"),
    # 魏
    Era("黃初", 220, 226, "wei"),
    Era("太和", 227, 233, "wei"),
    Era("青龍", 233, 237, "wei"),
    Era("景初", 237, 239, "wei"),
    Era("正始", 240, 249, "wei"),
    Era("嘉平", 249, 254, "wei"),
    Era("正元", 254, 256, "wei"),
    Era("甘露", 256, 260, "wei"),
    Era("景元", 260, 264, "wei"),
    Era("咸熙", 264, 265, "wei"),
    # 蜀
    Era("章武", 221, 223, "shu"),
    Era("建興", 223, 237, "shu"),
    Era("延熙", 238, 257, "shu"),
    Era("景耀", 258, 263, "shu"),
    Era("炎興", 263, 263, "shu"),
    # 吳
    Era("黃武", 222, 229, "wu"),
    Era("黃龍", 229, 231, "wu"),
    Era("嘉禾", 232, 238, "wu"),
    Era("赤烏", 238, 251, "wu"),
    Era("太元", 251, 252, "wu"),
    Era("神鳳", 252, 252, "wu"),
    Era("建興", 252, 253, "wu"),    # homonym with shu's 建興 (223–237)
    Era("五鳳", 254, 256, "wu"),
    Era("太平", 256, 258, "wu"),
    Era("永安", 258, 264, "wu"),
    Era("元興", 264, 265, "wu"),
    Era("甘露", 265, 266, "wu"),    # homonym with wei's 甘露 (256–260)
    Era("寶鼎", 266, 269, "wu"),
    Era("建衡", 269, 271, "wu"),
    Era("鳳凰", 272, 274, "wu"),
    Era("天冊", 275, 275, "wu"),
    Era("天璽", 276, 276, "wu"),
    Era("天紀", 277, 280, "wu"),
    # 西晉 (only the eras up through 三国 unification at 太康元年 = 280 — 资治通鉴 reaches that)
    Era("泰始", 265, 274, "jin"),    # 晉武帝
    Era("咸寧", 275, 279, "jin"),
    Era("太康", 280, 289, "jin"),
)

_DIGITS = {"元": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def chinese_to_int(s: str) -> Optional[int]:
    """Parse a Chinese year/month ordinal (元, 一-九, 十, 十一-九十九). None if unrecognized."""
    if not s:
        return None
    if s in _DIGITS:
        return _DIGITS[s]
    if s == "十":
        return 10
    if s.startswith("十") and len(s) == 2:
        units = _DIGITS.get(s[1])
        return 10 + units if units is not None else None
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _DIGITS.get(head)
        if tens is None or tens > 9:
            return None
        if not tail:
            return tens * 10
        if len(tail) != 1:
            return None
        units = _DIGITS.get(tail)
        return tens * 10 + units if units is not None else None
    return None


def chinese_to_month(s: str) -> Optional[int]:
    """Parse a Chinese month token (正, 一-十, 十一, 十二). None if unrecognized."""
    if s == "正":
        return 1
    n = chinese_to_int(s)
    if n is not None and 1 <= n <= 12:
        return n
    return None


_ERA_INDEX: dict[str, list[Era]] = {}
for _e in ERAS:
    _ERA_INDEX.setdefault(_e.name, []).append(_e)

_DYNASTY_FOR_BOOK = {"wei": "wei", "shu": "shu", "wu": "wu", "hhs": "han", "zztj": "zztj"}

# Per-book set of accepted dynasty contexts for era resolution. 三国志 and 后汉书
# books each have a "home" dynasty + 漢; 资治通鉴 spans every relevant dynasty
# because it's编年 chronicle of all of them.
_ACCEPTED_DYNASTIES_FOR_BOOK = {
    "wei": {"wei", "han"},
    "shu": {"shu", "han"},
    "wu": {"wu", "han"},
    "hhs": {"han"},
    "zztj": {"han", "wei", "shu", "wu", "jin"},
}


def resolve_era(era_name: str, era_year: int, *, book: str) -> Optional[Era]:
    """Resolve `(era_name, era_year)` to a specific Era record using book context.

    Returns None if the era is unknown, the year is outside the era's range, or the
    candidates remain ambiguous after applying the book's allowed dynasties.
    """
    candidates = _ERA_INDEX.get(era_name, [])
    if not candidates or era_year < 1:
        return None
    accepted = _ACCEPTED_DYNASTIES_FOR_BOOK.get(book)
    if accepted is None:
        return None
    matching = [c for c in candidates if c.dynasty in accepted]
    matching = [c for c in matching if era_year <= (c.end_ad - c.start_ad + 1)]
    if len(matching) != 1:
        return None
    return matching[0]


def to_ad(era: Era, era_year: int) -> int:
    """Year `era_year` of `era` → AD year. Year 1 = era.start_ad."""
    return era.start_ad + era_year - 1


_YEAR_CHAR_CLASS = "[元一二三四五六七八九十]"
_DYNASTY_PREFIX = "(?:漢|魏|蜀|吳)?"
_ERA_NAMES = sorted(_ERA_INDEX.keys(), key=len, reverse=True)
_DATE_RE = re.compile(
    rf"({_DYNASTY_PREFIX})({'|'.join(re.escape(n) for n in _ERA_NAMES)})({_YEAR_CHAR_CLASS}+)年"
)
_MONTH_RE = re.compile(r"(?P<season>春|夏|秋|冬)?(?P<month>正|[一二三四五六七八九十]+)月")


@dataclass
class DateMatch:
    """A resolved temporal reference. Used for both absolute and relative forms."""
    at: int
    length: int
    surface: str
    kind: str                       # "absolute" | "relative"
    resolution: str                 # absolute | bare_year | bare_month | this_year | next_year | prev_year
    year_ad: Optional[int]          # always set after successful resolution
    era: Optional[Era] = None       # carry the resolved Era record (or None for cross-boundary 明年)
    era_year: Optional[int] = None
    month_chinese: Optional[str] = None
    month_ordinal: Optional[int] = None


@dataclass
class TimelineState:
    """Carries the most recent resolved reign-era position across segments."""
    era: Optional[Era] = None
    era_year: Optional[int] = None
    year_ad: Optional[int] = None

    def copy(self) -> "TimelineState":
        return TimelineState(self.era, self.era_year, self.year_ad)


def find_dates(text: str, *, book: str) -> list[DateMatch]:
    """Scan `text` for absolute date references and return resolved DateMatch records."""
    results: list[DateMatch] = []
    for m in _DATE_RE.finditer(text):
        era_name = m.group(2)
        year_str = m.group(3)
        year_int = chinese_to_int(year_str)
        if year_int is None:
            continue
        era = resolve_era(era_name, year_int, book=book)
        if era is None:
            continue
        tail = text[m.end():]
        mm = _MONTH_RE.match(tail)
        if mm:
            month_chinese = mm.group(0)
            month_ord = chinese_to_month(mm.group("month"))
            surface = m.group(0) + month_chinese
        else:
            month_chinese = None
            month_ord = None
            surface = m.group(0)
        results.append(DateMatch(
            at=m.start(), length=len(surface), surface=surface,
            kind="absolute", resolution="absolute",
            year_ad=to_ad(era, year_int),
            era=era, era_year=year_int,
            month_chinese=month_chinese, month_ordinal=month_ord,
        ))
    return results


_BARE_YEAR_RE = re.compile(rf"({_YEAR_CHAR_CLASS}+)年")
_THIS_YEAR_RE = re.compile(r"是歲|是年|其年")
_NEXT_YEAR_RE = re.compile(r"明年")
_PREV_YEAR_RE = re.compile(r"去年|前年")
_BARE_MONTH_AT_RE = re.compile(r"(?:春|夏|秋|冬)?(?:正|[一二三四五六七八九十]+)月")


def _try_attach_month(text: str, after_pos: int) -> tuple[Optional[str], Optional[int], int]:
    """If a month token starts exactly at `after_pos`, return (surface, ordinal, length); else (None, None, 0)."""
    mm = _BARE_MONTH_AT_RE.match(text, after_pos)
    if not mm or mm.start() != after_pos:
        return None, None, 0
    inner = mm.group(0)
    # extract the digit portion (after optional season prefix)
    inner_match = re.match(r"(?:(?P<season>春|夏|秋|冬))?(?P<digits>正|[一二三四五六七八九十]+)月", inner)
    digits = inner_match.group("digits") if inner_match else None
    return inner, chinese_to_month(digits) if digits else None, len(inner)


def resolve_segment(text: str, *, book: str, state: Optional[TimelineState] = None) -> tuple[list[DateMatch], TimelineState]:
    """Find all temporal refs in segment text — absolute and relative — in document order.

    `state` carries timeline context from previous segments; on return it reflects
    the state after walking THIS segment so the caller can pass it to the next one.
    Refs that cannot be resolved (e.g. bare year before any absolute anchor, or
    bare year that doesn't fit the current era) are silently dropped.
    """
    state = state.copy() if state else TimelineState()

    abs_matches = find_dates(text, book=book)
    abs_ranges = [(m.at, m.at + m.length) for m in abs_matches]
    def in_abs(pos: int) -> bool:
        return any(s <= pos < e for s, e in abs_ranges)

    candidates: list[tuple[int, int, str, dict]] = []  # (at, prio, kind_label, info)

    for d in abs_matches:
        candidates.append((d.at, 0, "absolute", {"match": d}))

    # bare year (digits + 年), optional trailing month
    for m in _BARE_YEAR_RE.finditer(text):
        if in_abs(m.start()):
            continue
        year_int = chinese_to_int(m.group(1))
        if year_int is None or year_int < 1:
            continue
        m_chinese, m_ord, m_len = _try_attach_month(text, m.end())
        candidates.append((m.start(), 1, "bare_year", {
            "year_int": year_int, "raw_surface": m.group(0) + (m_chinese or ""),
            "length": len(m.group(0)) + m_len,
            "month_chinese": m_chinese, "month_ordinal": m_ord,
        }))

    for m in _THIS_YEAR_RE.finditer(text):
        if in_abs(m.start()):
            continue
        candidates.append((m.start(), 2, "this_year", {
            "raw_surface": m.group(0), "length": len(m.group(0)),
        }))

    for m in _NEXT_YEAR_RE.finditer(text):
        if in_abs(m.start()):
            continue
        m_chinese, m_ord, m_len = _try_attach_month(text, m.end())
        candidates.append((m.start(), 3, "next_year", {
            "raw_surface": m.group(0) + (m_chinese or ""),
            "length": len(m.group(0)) + m_len,
            "month_chinese": m_chinese, "month_ordinal": m_ord,
        }))

    for m in _PREV_YEAR_RE.finditer(text):
        if in_abs(m.start()):
            continue
        candidates.append((m.start(), 4, "prev_year", {
            "raw_surface": m.group(0), "length": len(m.group(0)),
        }))

    # Pre-collect positions that bare years already consume (absolute + bare_year ranges) so a later
    # bare_month doesn't double-count a month already attached to the year.
    consumed_by_year = set()
    for at, _, kind, info in candidates:
        if kind in ("absolute", "bare_year", "next_year"):
            length = info["match"].length if kind == "absolute" else info["length"]
            for i in range(at, at + length):
                consumed_by_year.add(i)

    for m in _BARE_MONTH_AT_RE.finditer(text):
        if in_abs(m.start()):
            continue
        if m.start() in consumed_by_year:
            continue
        # If this month immediately follows a "年" character that we already captured as bare_year,
        # the month is part of that bare year's surface — already handled.
        if m.start() > 0 and text[m.start() - 1] == "年":
            continue
        digits_match = re.match(r"(?:(?P<season>春|夏|秋|冬))?(?P<digits>正|[一二三四五六七八九十]+)月", m.group(0))
        digits = digits_match.group("digits") if digits_match else None
        month_ord = chinese_to_month(digits) if digits else None
        if month_ord is None:
            continue
        candidates.append((m.start(), 5, "bare_month", {
            "raw_surface": m.group(0), "length": len(m.group(0)),
            "month_chinese": m.group(0), "month_ordinal": month_ord,
        }))

    candidates.sort(key=lambda c: (c[0], c[1]))

    out: list[DateMatch] = []
    for at, _, kind, info in candidates:
        if kind == "absolute":
            d = info["match"]
            out.append(d)
            # 编年体 (e.g. 资治通鉴) reads strictly forward — an in-text absolute
            # ref to an earlier year is a flashback for context, not a state move.
            # Bio-style works (sanguozhi/houhanshu) can also do this, e.g. an obituary
            # paragraph that references early career years; the same backward-only
            # guard avoids polluting subsequent paragraphs' state. We still emit
            # the annotation at the flashback's actual year_ad.
            if (state.year_ad is not None and d.year_ad < state.year_ad):
                continue
            state = TimelineState(era=d.era, era_year=d.era_year, year_ad=d.year_ad)
            continue

        if kind == "bare_year":
            if state.era is None:
                continue
            year_int = info["year_int"]
            era_length = state.era.end_ad - state.era.start_ad + 1
            if year_int > era_length:
                continue
            year_ad = state.era.start_ad + year_int - 1
            out.append(DateMatch(
                at=at, length=info["length"], surface=info["raw_surface"],
                kind="relative", resolution="bare_year",
                year_ad=year_ad, era=state.era, era_year=year_int,
                month_chinese=info.get("month_chinese"),
                month_ordinal=info.get("month_ordinal"),
            ))
            state = TimelineState(era=state.era, era_year=year_int, year_ad=year_ad)

        elif kind == "this_year":
            if state.year_ad is None:
                continue
            out.append(DateMatch(
                at=at, length=info["length"], surface=info["raw_surface"],
                kind="relative", resolution="this_year",
                year_ad=state.year_ad, era=state.era, era_year=state.era_year,
            ))

        elif kind == "next_year":
            if state.year_ad is None:
                continue
            new_year_ad = state.year_ad + 1
            new_era = state.era
            new_era_year = None
            if new_era and new_era.start_ad <= new_year_ad <= new_era.end_ad:
                new_era_year = new_year_ad - new_era.start_ad + 1
            else:
                new_era = None
            out.append(DateMatch(
                at=at, length=info["length"], surface=info["raw_surface"],
                kind="relative", resolution="next_year",
                year_ad=new_year_ad, era=new_era, era_year=new_era_year,
                month_chinese=info.get("month_chinese"),
                month_ordinal=info.get("month_ordinal"),
            ))
            state = TimelineState(era=new_era, era_year=new_era_year, year_ad=new_year_ad)

        elif kind == "prev_year":
            if state.year_ad is None:
                continue
            new_year_ad = state.year_ad - 1
            new_era = state.era
            new_era_year = None
            if new_era and new_era.start_ad <= new_year_ad <= new_era.end_ad:
                new_era_year = new_year_ad - new_era.start_ad + 1
            else:
                new_era = None
            out.append(DateMatch(
                at=at, length=info["length"], surface=info["raw_surface"],
                kind="relative", resolution="prev_year",
                year_ad=new_year_ad, era=new_era, era_year=new_era_year,
            ))
            # don't update state; 去年 is a backward reference, not a forward move

        elif kind == "bare_month":
            if state.year_ad is None:
                continue
            out.append(DateMatch(
                at=at, length=info["length"], surface=info["raw_surface"],
                kind="relative", resolution="bare_month",
                year_ad=state.year_ad, era=state.era, era_year=state.era_year,
                month_chinese=info["month_chinese"],
                month_ordinal=info["month_ordinal"],
            ))

    return out, state
