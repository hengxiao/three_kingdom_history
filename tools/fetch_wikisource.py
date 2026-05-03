"""Fetch a 三國志 chapter from zh.wikisource.org, strip 裴注 (〈...〉), write Markdown + raw snapshot.

The Wikisource layout (verified 2026-05-01 against /wiki/三國志/卷05):
    <table class="ws-header"> ... <td align="center">...<br />魏書·后妃傳</td> ...
    <div class="mw-parser-output">
        <p>...正文 〈裴注〉 正文...</p>
        <p>...</p>
    </div>

裴注 are wrapped in full-width angle brackets 〈 〉 (U+3008/U+3009). They appear inline
within the canonical text. We strip them out for texts/ and capture position+content
on each WSParagraph (annotations not yet written to annotations/).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path

from tools.segment import file_segments_sha256, parse_text, render_with_frontmatter

USER_AGENT = "three_kingdom_history-bot/0.1 (research; +https://github.com/)"

# The header has a center cell that contains both the 三國志 link and, after a <br />,
# the chapter title. Wikisource pages vary considerably in punctuation:
#   "魏書·后妃傳"             (wei style — middle-dot U+00B7)
#   "魏書•武帝紀"             (wei/01 — bullet U+2022)
#   "《吳書》·妃嬪傳"           (wu/05 — book in 《》)
#   "蜀書四 二主妃子傳"        (shu — book + ordinal + space + chapter)
#   "魏書三十 烏丸鮮卑東夷傳第三十" (wei/30 — trailing "第N" suffix)
#   "呉書·孫破虜討逆傳"        (wu/01 — Japanese 呉 instead of 吳)
#   "卷四十九 吳書四 劉繇..."    (wu/04 — leading "卷N " prefix)
# We capture every <br />-prefixed line and pick the first that names a book,
# then normalize to "<book>·<chapter>".
# Capture each <br />-prefixed line up to the next <br /> or </td>. Inner HTML is
# kept so we can strip nested <b>...</b> later — a few wei chapters wrap the chapter
# name in <b> while the trailing "第N" lives outside, so we have to span tags.
_TITLE_CANDIDATE_RE = re.compile(r"<br\s*/?>(.*?)(?=<br\s*/?>|</td)", re.DOTALL)
_ORDINAL_CHARS = "零一二三四五六七八九十百千０-９0-9"
_DOT_CHARS = "·•・‧"  # middle-dot variants seen in Wikisource
_TITLE_NORMALIZE_RE = re.compile(
    rf"^(魏書|蜀書|吳書)[{_ORDINAL_CHARS}]*\s*[{_DOT_CHARS}\s]+\s*(.+?)(?:\s*第[{_ORDINAL_CHARS}]+)?$"
)
_VOLUME_PREFIX_RE = re.compile(rf"^卷[{_ORDINAL_CHARS}]+[\s{_DOT_CHARS}]+")
# Wikisource has rendered both `class="mw-parser-output"` (older 三國志 pages) and
# `class="mw-content-ltr mw-parser-output"` (newer 後漢書 pages); accept both.
_BODY_RE = re.compile(
    r'<div class="[^"]*\bmw-parser-output\b[^"]*"[^>]*>(.*?)(?:<div class="printfooter"|<div id="catlinks")',
    re.DOTALL,
)
_P_TAG_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_MIN_CJK_HAN = 5  # paragraphs with fewer than this many CJK Han chars are treated as nav/template noise


@dataclass
class WSAnnotation:
    at: int  # character index into the canonical paragraph (0-based) where the annotation is anchored
    text: str  # plain text of the annotation, whitespace-stripped


@dataclass
class WSParagraph:
    para_no: int  # document-order index, 1-based, monotonic
    main_text: str  # canonical 正文, whitespace-stripped (per format §6.1)
    annotations: list[WSAnnotation] = field(default_factory=list)


@dataclass
class WSChapter:
    title: str  # canonical Sanguozhi chapter title, e.g. "魏書·后妃傳"
    paragraphs: list[WSParagraph]
    parse_warnings: list[str] = field(default_factory=list)
    """Non-fatal issues encountered while parsing (e.g. unbalanced 〈〉 in source)."""


def _ascii_encode_url(url: str) -> str:
    """Percent-encode any non-ASCII chars in the path/query so urllib can send the request."""
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%")
    query = urllib.parse.quote(parts.query, safe="=&%")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def fetch(url: str, *, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(_ascii_encode_url(url), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _has_min_han(text: str, min_chars: int = _MIN_CJK_HAN) -> bool:
    n = 0
    for c in text:
        if 0x4E00 <= ord(c) <= 0x9FFF:
            n += 1
            if n >= min_chars:
                return True
    return False


def _strip_to_text(html_fragment: str) -> str:
    """Strip HTML tags + unescape entities + remove all whitespace (per format §6.1)."""
    txt = unescape(_TAG_RE.sub("", html_fragment))
    return re.sub(r"\s+", "", txt)


_PARA_SENTINEL = ""  # placeholder for paragraph break inside the joined body stream


def _split_main_and_annotations(text: str) -> tuple[str, list[WSAnnotation]]:
    """Strict per-paragraph: separate top-level 〈...〉 from 正文; fail on imbalance.

    Inner 〈...〉 (e.g. cross-references inside a Pei Songzhi quote) stay inside the
    enclosing annotation text. Raises ValueError if brackets don't balance.
    """
    main: list[str] = []
    annotations: list[WSAnnotation] = []
    ann_buf: list[str] = []
    depth = 0
    main_len = 0
    for i, c in enumerate(text):
        if c == "〈":
            if depth > 0:
                ann_buf.append(c)
            depth += 1
        elif c == "〉":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced '〉' at position {i} in text starting {text[:40]!r}")
            if depth == 0:
                annotations.append(WSAnnotation(at=main_len, text="".join(ann_buf)))
                ann_buf = []
            else:
                ann_buf.append(c)
        else:
            if depth == 0:
                main.append(c)
                main_len += 1
            else:
                ann_buf.append(c)
    if depth != 0:
        raise ValueError(f"unbalanced '〈' (depth {depth}) in text starting {text[:40]!r}")
    return "".join(main), annotations


def _orphan_bracket_positions(text: str) -> tuple[set[int], set[int]]:
    """Find positions of unmatched 〈 (no later 〉 to close them) and unmatched 〉.

    Used to skip Wikisource markup defects: if a chapter has one stray bracket,
    we treat just that one position as literal text and still extract annotations
    from all the balanced pairs around it.
    """
    stack: list[int] = []
    orphan_close: set[int] = set()
    for i, c in enumerate(text):
        if c == "〈":
            stack.append(i)
        elif c == "〉":
            if stack:
                stack.pop()
            else:
                orphan_close.add(i)
    return set(stack), orphan_close


def _process_paragraphs_strict(p_texts: list[str]) -> tuple[list[tuple[str, list[WSAnnotation]]], list[str]]:
    """Cross-paragraph state machine. 裴注 may span multiple <p> tags.

    Joins paragraphs with a sentinel char; outside an annotation the sentinel ends
    the current paragraph; inside an annotation it is dropped (the annotation
    keeps growing into the next paragraph). Annotations that span paragraphs cause
    the affected <p> tags to merge into a single output paragraph (the 〈 anchor is
    in the first one).

    If the source has unbalanced brackets, those exact orphan positions are treated
    as literal text (not bracket markers) so the rest of the chapter parses cleanly.
    Returns (paragraphs, warnings).
    """
    joined = _PARA_SENTINEL.join(p_texts)
    orphan_open, orphan_close = _orphan_bracket_positions(joined)

    warnings: list[str] = []
    if orphan_open or orphan_close:
        warnings.append(
            f"source has unbalanced brackets: {len(orphan_open)} stray '〈', "
            f"{len(orphan_close)} stray '〉' — treating them as literal text"
        )

    paragraphs: list[tuple[str, list[WSAnnotation]]] = []
    main: list[str] = []
    anns: list[WSAnnotation] = []
    ann_buf: list[str] = []
    depth = 0
    main_len = 0
    for i, c in enumerate(joined):
        is_orphan = (c == "〈" and i in orphan_open) or (c == "〉" and i in orphan_close)
        if c == _PARA_SENTINEL:
            if depth == 0:
                paragraphs.append(("".join(main), anns))
                main, anns, main_len = [], [], 0
            # else: paragraph break inside an annotation just continues it
        elif c == "〈" and not is_orphan:
            if depth > 0:
                ann_buf.append(c)
            depth += 1
        elif c == "〉" and not is_orphan:
            depth -= 1
            if depth == 0:
                anns.append(WSAnnotation(at=main_len, text="".join(ann_buf)))
                ann_buf = []
            else:
                ann_buf.append(c)
        else:
            # Plain char (or an orphan bracket treated as literal)
            if depth == 0:
                main.append(c)
                main_len += 1
            else:
                ann_buf.append(c)
    paragraphs.append(("".join(main), anns))
    if depth != 0:
        raise ValueError(f"internal error: depth {depth} after orphan-aware pass")
    return paragraphs, warnings


_LIXIAN_MARKER_CHARS = "零一二三四五六七八九十百0-9０-９"
_LIXIAN_MARKER_RE = re.compile(rf"(?<!注)\[([{_LIXIAN_MARKER_CHARS}]+)\]")
_LIXIAN_ANNOTATION_RE = re.compile(rf"注\[([{_LIXIAN_MARKER_CHARS}]+)\]")


def _detect_lixian_bracket_convention(p_texts: list[str]) -> bool:
    """Detect 后汉书 chapters that use [X] / 注[X] for 李贤注 (no 〈〉 markup).

    Strategy: prefer 〈〉 wherever present; otherwise fall back to bracketed
    convention as long as at least one `注[X]` block exists.
    """
    if any("〈" in t for t in p_texts):
        return False
    return any(_LIXIAN_ANNOTATION_RE.search(t) for t in p_texts)


def _process_paragraphs_lixian_brackets(p_texts: list[str]) -> list[tuple[str, list[WSAnnotation]]]:
    """Parse 后汉书 chapters that use the [一] / 注[一] convention.

    The canonical text contains inline markers like [一], [二], … that point at
    annotations whose content lives later in the same chapter, prefixed with
    `注[X]<annotation text>`. Each `注[X]` block continues until the next
    `注[Y]` or the end of its paragraph.

    Markers and annotations are paired in document order (the kth marker → kth
    annotation). Ordinals are not used for pairing — they only serve as a
    sanity hint, and when they disagree with the sequential pairing we still
    take the sequential pair (matches Wikisource's actual practice on these
    chapters, where ordinals reset across sub-biographies).
    """
    # 1) For each paragraph, split into the canonical-with-markers portion (before
    #    the first `注[X]`) and a sequence of annotation texts.
    canonicals: list[str] = []   # cleaned canonical text per output paragraph
    markers: list[tuple[int, int]] = []  # (canonical_paragraph_idx, char_pos_in_clean)
    annotations_inline: list[str] = []   # ordered list of annotation texts

    for ptext in p_texts:
        ann_matches = list(_LIXIAN_ANNOTATION_RE.finditer(ptext))
        if not ann_matches:
            canonical_part = ptext
        else:
            canonical_part = ptext[: ann_matches[0].start()]
            for i, m in enumerate(ann_matches):
                start = m.end()
                end = ann_matches[i + 1].start() if i + 1 < len(ann_matches) else len(ptext)
                ann_text = ptext[start:end].strip()
                if ann_text:
                    annotations_inline.append(ann_text)

        # Strip [X] markers from canonical_part, recording their cleaned positions.
        clean_chars: list[str] = []
        local_markers: list[int] = []
        i = 0
        while i < len(canonical_part):
            m = _LIXIAN_MARKER_RE.match(canonical_part, i)
            if m:
                local_markers.append(len("".join(clean_chars)))
                i = m.end()
            else:
                clean_chars.append(canonical_part[i])
                i += 1
        clean = "".join(clean_chars)
        if not clean.strip():
            continue
        canonical_idx = len(canonicals)
        canonicals.append(clean)
        for pos in local_markers:
            markers.append((canonical_idx, pos))

    # 2) Pair markers with annotations sequentially. Extras on either side are dropped
    #    (silent — wikisource sometimes has stray markers / extra annotations).
    paragraphs_anns: list[list[WSAnnotation]] = [[] for _ in canonicals]
    for k, (canonical_idx, char_pos) in enumerate(markers):
        if k >= len(annotations_inline):
            break
        paragraphs_anns[canonical_idx].append(
            WSAnnotation(at=char_pos, text=annotations_inline[k])
        )

    return list(zip(canonicals, paragraphs_anns))


def _process_paragraphs_lenient(p_texts: list[str]) -> list[tuple[str, list[WSAnnotation]]]:
    """Fallback for chapters with truly unbalanced brackets in the source data.

    Strips balanced 〈...〉 pairs iteratively (handles nesting). Orphan 〈 or 〉 stay
    in the canonical text — flagged for manual cleanup by check.py downstream.
    Returns empty annotations: extraction is given up for the whole chapter; rerun
    a future, smarter parser on sources/wikisource/<…>.html to recover them.
    """
    out: list[tuple[str, list[WSAnnotation]]] = []
    for text in p_texts:
        cleaned = text
        while True:
            new = re.sub(r"〈[^〈〉]*〉", "", cleaned)
            if new == cleaned:
                break
            cleaned = new
        out.append((cleaned, []))
    return out


def _normalize_title(raw: str) -> str:
    """Normalize Wikisource header title to '<book>·<chapter>' form (sanguozhi)."""
    s = raw.replace("《", "").replace("》", "").strip()
    s = s.replace("呉", "吳").replace("吴", "吳")  # Japanese / simplified → traditional
    s = _VOLUME_PREFIX_RE.sub("", s)              # strip leading "卷N " (e.g. "卷四十九 吳書...")
    m = _TITLE_NORMALIZE_RE.match(s)
    return f"{m.group(1)}·{m.group(2)}" if m else s


_HHS_VOLUME_PREFIX_RE = re.compile(rf"^卷[{_ORDINAL_CHARS}]+[上中下]?[·•・‧\s　]+")
_HHS_CATEGORY_PREFIX_RE = re.compile(rf"^(帝紀|本紀|皇后紀)第[{_ORDINAL_CHARS}]+[上中下]?[\s　]+")
_HHS_TRAILING_ORDINAL_RE = re.compile(rf"\s*第[{_ORDINAL_CHARS}]+[上中下]?\s*$")


def _normalize_title_hhs(raw: str) -> str:
    """Normalize a Wikisource 後漢書 header into a clean chapter title.

    Strips the leading 卷N(上|下)?(·| ) prefix, the trailing 第N(上|中|下)? ordinal,
    and a leading "<category>第N "  block (so e.g. 帝紀第八　孝靈皇帝 → 孝靈皇帝).
    Other forms — 列傳 with the surname/name before, e.g. 馬融列傳 — are left as-is
    after the trailing-ordinal strip.
    """
    s = raw.replace("《", "").replace("》", "").strip()
    s = _HHS_VOLUME_PREFIX_RE.sub("", s)
    s = _HHS_CATEGORY_PREFIX_RE.sub("", s)
    s = _HHS_TRAILING_ORDINAL_RE.sub("", s)
    return s.strip()


_HHS_TITLE_KEYWORDS = ("帝紀", "本紀", "皇后紀", "列傳", "列传", "志第", "紀第", "傳第", "传第")


def _extract_title(html: str, *, work: str = "sanguozhi") -> str:
    """Find the first wikisource-header line that names a chapter, normalized for `work`."""
    for m in _TITLE_CANDIDATE_RE.finditer(html):
        cand = unescape(_TAG_RE.sub("", m.group(1)))
        cand = re.sub(r"\s+", " ", cand).strip()
        if not cand:
            continue
        cand_canon = cand.replace("呉", "吳").replace("吴", "吳")
        if work == "houhanshu":
            if not any(kw in cand_canon for kw in _HHS_TITLE_KEYWORDS):
                continue
            return _normalize_title_hhs(cand)
        # default: sanguozhi
        if not any(book in cand_canon for book in ("魏書", "蜀書", "吳書")):
            continue
        normalized = _normalize_title(cand)
        if "·" not in normalized:
            continue
        return normalized
    return ""


def parse_wikisource_html(html: str, *, work: str = "sanguozhi") -> WSChapter:
    """Extract canonical title + paragraphs (annotations stripped) from a Wikisource page."""
    title = _extract_title(html, work=work)

    bm = _BODY_RE.search(html)
    body = bm.group(1) if bm else html

    p_texts: list[str] = []
    for pm in _P_TAG_RE.finditer(body):
        text = _strip_to_text(pm.group(1))
        if text and _has_min_han(text):
            p_texts.append(text)

    if not p_texts:
        raise ValueError("no body paragraphs found — Wikisource layout may have changed")

    parse_warnings: list[str] = []
    if work == "houhanshu" and _detect_lixian_bracket_convention(p_texts):
        # Some 后汉书 chapters mark 李贤注 as 注[一] blocks instead of 〈...〉.
        processed = _process_paragraphs_lixian_brackets(p_texts)
    else:
        try:
            processed, parse_warnings = _process_paragraphs_strict(p_texts)
        except ValueError as e:
            parse_warnings = [f"strict bracket parse failed ({e}); falling back to lenient"]
            processed = _process_paragraphs_lenient(p_texts)

    paragraphs: list[WSParagraph] = []
    for main_text, anns in processed:
        if not main_text or not _has_min_han(main_text):
            continue
        paragraphs.append(WSParagraph(
            para_no=len(paragraphs) + 1,
            main_text=main_text,
            annotations=anns,
        ))

    if not paragraphs:
        raise ValueError("after annotation stripping, no body paragraphs remain")

    return WSChapter(title=title, paragraphs=paragraphs, parse_warnings=parse_warnings)


def render_markdown(
    chapter: WSChapter,
    *,
    work: str,
    work_title: str,
    book: str,
    book_title: str,
    work_prefix: str,
    juan: int,
    title: str,
    author: str,
    source_url: str,
    source_sha256: str,
    source_retrieved: str,
) -> str:
    """Render a WSChapter as a texts/ Markdown file (frontmatter + body + segments_sha256)."""
    body_lines: list[str] = []
    for p in chapter.paragraphs:
        body_lines.append(f'<a id="{work_prefix}.{juan}.p{p.para_no}"></a>')
        body_lines.append(p.main_text)
        body_lines.append("")
    body = "\n".join(body_lines)

    fm: dict = {
        "work": work,
        "work_title": work_title,
        "book": book,
        "book_title": book_title,
        "juan": juan,
        "title": title,
        "author": author,
        "script": "traditional",
        "source": {
            "id": "wikisource",
            "url": source_url,
            "retrieved": source_retrieved,
            "sha256": source_sha256,
        },
    }
    intermediate = render_with_frontmatter(fm, body)
    parsed = parse_text(intermediate)
    fm["segments_sha256"] = file_segments_sha256(parsed.segments)
    return render_with_frontmatter(fm, body)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch a Wikisource 三國志 chapter and write Markdown + raw HTML snapshot.")
    p.add_argument("--ws-juan", type=int, required=True, help="global juan number 1–65")
    p.add_argument("--work-prefix", required=True)
    p.add_argument("--book", required=True)
    p.add_argument("--book-title", required=True)
    p.add_argument("--juan", type=int, required=True, help="local juan within the book")
    p.add_argument("--title", default=None, help="override chapter title (defaults to auto-extracted)")
    p.add_argument("--author", default="陳壽")
    p.add_argument("--out-text", type=Path, required=True)
    p.add_argument("--out-source", type=Path, required=True)
    p.add_argument("--retrieved", default=None)
    p.add_argument("--no-fetch", action="store_true")
    args = p.parse_args(argv)

    url = f"https://zh.wikisource.org/wiki/三國志/卷{args.ws_juan:02d}"
    if args.no_fetch:
        raw = args.out_source.read_bytes()
    else:
        raw = fetch(url)
        args.out_source.parent.mkdir(parents=True, exist_ok=True)
        args.out_source.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()

    chapter = parse_wikisource_html(raw.decode("utf-8", errors="replace"))
    title = args.title or chapter.title
    if not title:
        print(f"ERROR: could not auto-extract title from {url}; pass --title", file=sys.stderr)
        return 1

    md = render_markdown(
        chapter,
        work="sanguozhi",
        work_title="三國志",
        book=args.book,
        book_title=args.book_title,
        work_prefix=args.work_prefix,
        juan=args.juan,
        title=title,
        author=args.author,
        source_url=url,
        source_sha256=sha,
        source_retrieved=args.retrieved or date.today().isoformat(),
    )
    args.out_text.parent.mkdir(parents=True, exist_ok=True)
    args.out_text.write_text(md, encoding="utf-8")
    print(f"wrote {args.out_text} ({len(chapter.paragraphs)} segments) source_sha256={sha}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
