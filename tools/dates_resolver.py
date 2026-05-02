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

_DYNASTY_FOR_BOOK = {"wei": "wei", "shu": "shu", "wu": "wu"}


def resolve_era(era_name: str, era_year: int, *, book: str) -> Optional[Era]:
    """Resolve `(era_name, era_year)` to a specific Era record using book context.

    Returns None if the era is unknown, the year is outside the era's range, or the
    candidates remain ambiguous after applying the book's allowed dynasties.
    """
    candidates = _ERA_INDEX.get(era_name, [])
    if not candidates or era_year < 1:
        return None
    own = _DYNASTY_FOR_BOOK.get(book)
    if not own:
        return None
    accepted = {own, "han"}
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
    at: int                 # character offset in the input string
    length: int             # length of the matched surface (era + year [+ month])
    surface: str            # the matched substring
    era: Era                # resolved Era record
    era_year: int           # parsed Chinese era-year, e.g. 五 → 5
    year_ad: int            # Gregorian AD year
    month_chinese: Optional[str] = None
    month_ordinal: Optional[int] = None  # 1–12 if month captured


def find_dates(text: str, *, book: str) -> list[DateMatch]:
    """Scan `text` for absolute date references and return resolved DateMatch records.

    Skips matches whose era is unknown or whose year is out of range — those are
    likely false positives or eras outside our table (early Han, etc.).
    """
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
        # Optional immediately-following month (e.g. "建安五年春正月")
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
            at=m.start(),
            length=len(surface),
            surface=surface,
            era=era,
            era_year=year_int,
            year_ad=to_ad(era, year_int),
            month_chinese=month_chinese,
            month_ordinal=month_ord,
        ))
    return results
