"""Fetch a ctext.org Sanguozhi chapter, strip 裴注, write Markdown + raw snapshot.

The HTML layout (verified 2026-05-01 against /sanguozhi/1):
    <a href="sanguozhi/{ctext_juan}#n{node_id}" ...>{para_num}</a>   ← visible paragraph number
    ...
    <td class="ctext">
        <div id="comm{node_id}"></div>
        正文  <span class="inlinecomment">裴注</span>  正文 ...
    </td>

We extract canonical 正文 (with all `<span class="inlinecomment">` stripped) into texts/,
keeping the inline comments only as positional annotations on the returned ParsedChapter
(not yet written to annotations/ — that's a later step).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path

from tools.segment import file_segments_sha256, parse_text, render_with_frontmatter

USER_AGENT = "three_kingdom_history-bot/0.1 (research; +https://github.com/)"

_PARA_NUM_RE = re.compile(r'<a href="sanguozhi/(\d+)#n\d+"[^>]*>(\d+)</a>')
_PARA_TD_RE = re.compile(r'<td class="ctext">(.*?)</td>', re.DOTALL)
_NODE_RE = re.compile(r'<div id="comm(\d+)"></div>')
_INLINECOMMENT_RE = re.compile(r'<span class="inlinecomment">(.*?)</span>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# Each paragraph row has a label cell like <td ... class="ctext opt">武帝紀:</td>
_TITLE_RE = re.compile(r'<td[^>]*class="ctext opt"[^>]*>([^<:]+):</td>')
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)


@dataclass
class CtextAnnotation:
    at: int  # character index into the canonical paragraph (0-based) where this annotation is anchored
    text: str  # plain text of the annotation


@dataclass
class CtextParagraph:
    para_no: int  # document-order index, 1-based, monotonic across sub-sections
    node_id: str  # ctext internal node id, for back-reference
    section: str  # sub-section label (e.g. "孫堅傳"); equals chapter title for solo bios
    ctext_display_no: int  # the number ctext shows; restarts at sub-section boundaries
    main_text: str  # canonical 正文 (with whitespace stripped per format §6.1)
    annotations: list[CtextAnnotation] = field(default_factory=list)


@dataclass
class ParsedChapter:
    paragraphs: list[CtextParagraph]
    section_titles: list[str] = field(default_factory=list)
    """All distinct labels from <td class="ctext opt">XXX:</td>, in first-occurrence order.

    For solo biographies this contains a single item (e.g. ['武帝紀']). For combined
    biographies ctext labels each sub-section separately (e.g. ['孫堅傳', '孫策傳']);
    the canonical Sanguozhi chapter title (孫破虜討逆傳) does not appear on the page
    and must be supplied via batch_fetch config when wanted.
    """

    @property
    def title(self) -> str:
        """Best-effort chapter title. Single label, or sub-labels joined by '、'."""
        return "、".join(self.section_titles)


def fetch(url: str, *, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _plain(html_fragment: str) -> str:
    """Strip tags + unescape entities + remove all whitespace (per format §6.1)."""
    txt = unescape(_TAG_RE.sub("", html_fragment))
    return re.sub(r"\s+", "", txt)


def parse_ctext_html(html: str, ctext_juan: int) -> ParsedChapter:
    """Extract canonical paragraphs (annotations stripped) from a ctext sanguozhi page.

    Paragraph IDs use document-order numbering (1, 2, ...) which is monotonic across
    sub-sections. ctext's own visible numbers restart at each sub-section in combined
    biographies, so we can't use them as primary keys; we keep them in `ctext_display_no`
    for back-reference.
    """
    expected_juan = str(ctext_juan)
    paragraphs: list[CtextParagraph] = []
    section_titles: list[str] = []
    seen_sections: set[str] = set()
    current_section = ""

    for trm in _TR_RE.finditer(html):
        tr = trm.group(1)
        # Section label (if this row carries one)
        sm = _TITLE_RE.search(tr)
        if sm:
            current_section = sm.group(1)
            if current_section not in seen_sections:
                seen_sections.add(current_section)
                section_titles.append(current_section)
        # Paragraph cell
        tdm = _PARA_TD_RE.search(tr)
        if not tdm:
            continue
        body = tdm.group(1)
        nm = _NODE_RE.search(body)
        if not nm:
            continue
        node_id = nm.group(1)
        # ctext-displayed paragraph number (restarts per sub-section)
        nm2 = _PARA_NUM_RE.search(tr)
        if nm2 and nm2.group(1) != expected_juan:
            raise ValueError(
                f"href references juan {nm2.group(1)} but expected {expected_juan}; "
                "is the ctext_juan argument correct?"
            )
        ctext_display_no = int(nm2.group(2)) if nm2 else 0

        body = _NODE_RE.sub("", body)
        main_parts: list[str] = []
        annotations: list[CtextAnnotation] = []
        cursor = 0
        running_len = 0
        for am in _INLINECOMMENT_RE.finditer(body):
            chunk = _plain(body[cursor:am.start()])
            main_parts.append(chunk)
            running_len += len(chunk)
            annotations.append(CtextAnnotation(at=running_len, text=_plain(am.group(1))))
            cursor = am.end()
        main_parts.append(_plain(body[cursor:]))
        main_text = "".join(main_parts)

        paragraphs.append(CtextParagraph(
            para_no=len(paragraphs) + 1,
            node_id=node_id,
            section=current_section,
            ctext_display_no=ctext_display_no,
            main_text=main_text,
            annotations=annotations,
        ))

    if not paragraphs:
        raise ValueError("no paragraph numbers found — page layout may have changed")
    return ParsedChapter(paragraphs=paragraphs, section_titles=section_titles)


def render_markdown(
    chapter: ParsedChapter,
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
    """Render a ParsedChapter as a texts/ Markdown file (frontmatter + body)."""
    paragraphs = [p for p in chapter.paragraphs if p.main_text]
    if not paragraphs:
        raise ValueError("chapter has no non-empty 正文 paragraphs after stripping annotations")

    body_lines: list[str] = []
    for p in paragraphs:
        seg_id = f"{work_prefix}.{juan}.p{p.para_no}"
        body_lines.append(f'<a id="{seg_id}"></a>')
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
            "id": "ctext",
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
    p = argparse.ArgumentParser(description="Fetch a ctext sanguozhi chapter and write Markdown + raw HTML snapshot.")
    p.add_argument("--ctext-juan", type=int, required=True, help="ctext juan number (1–65 for sanguozhi)")
    p.add_argument("--work-prefix", required=True, help="segment ID prefix, e.g. 'wei'")
    p.add_argument("--book", required=True, help="frontmatter book id, e.g. 'wei'")
    p.add_argument("--book-title", required=True, help="frontmatter book_title, e.g. '魏書'")
    p.add_argument("--juan", type=int, required=True, help="local juan number within the book")
    p.add_argument("--title", default=None, help="override chapter title (defaults to auto-extracted from page)")
    p.add_argument("--author", default="陳壽")
    p.add_argument("--out-text", type=Path, required=True)
    p.add_argument("--out-source", type=Path, required=True)
    p.add_argument("--retrieved", default=None, help="ISO date; defaults to today")
    p.add_argument("--no-fetch", action="store_true", help="reuse existing --out-source instead of fetching")
    args = p.parse_args(argv)

    url = f"https://ctext.org/sanguozhi/{args.ctext_juan}"
    if args.no_fetch:
        raw = args.out_source.read_bytes()
    else:
        raw = fetch(url)
        args.out_source.parent.mkdir(parents=True, exist_ok=True)
        args.out_source.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()

    chapter = parse_ctext_html(raw.decode("utf-8", errors="replace"), args.ctext_juan)

    title = args.title or chapter.title
    if not title:
        print(f"ERROR: could not auto-extract chapter title from {url}; pass --title", file=sys.stderr)
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
    n_kept = sum(1 for p in chapter.paragraphs if p.main_text)
    n_skipped = len(chapter.paragraphs) - n_kept
    print(
        f"wrote {args.out_text} ({n_kept} segments, {n_skipped} skipped as annotation-only) "
        f"source_sha256={sha}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
