"""Tests for tools/build_variants.py — uses synthetic ctext + wikisource HTML, no network."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from tools.build_variants import (
    SKIP_REASON_TOC,
    VariantResult,
    VariantSkip,
    _align_normalized,
    process_one,
    variants_path,
)
from tools.fetch_wikisource import render_markdown as ws_render_markdown
from tools.fetch_wikisource import parse_wikisource_html

WS_FIXTURE = Path(__file__).parent / "fixtures" / "wikisource_sample.html"
CTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "ctext_sample.html"


# ---------- _align_normalized ----------

def test_align_perfect_match():
    """Identical hash sequences map index-to-index."""
    a = ["h1", "h2", "h3"]
    b = ["h1", "h2", "h3"]
    assert _align_normalized(a, b) == {0: 0, 1: 1, 2: 2}


def test_align_with_extra_paragraph_in_source():
    """Source has an extra segment (e.g. annotation-only). Canonical → matching index, extra unmatched."""
    a = ["h1", "h2", "h3"]
    b = ["h1", "extra", "h2", "h3"]
    pairs = _align_normalized(a, b)
    assert pairs == {0: 0, 1: 2, 2: 3}


def test_align_with_extra_canonical():
    a = ["h1", "h2", "h3"]
    b = ["h1", "h3"]
    pairs = _align_normalized(a, b)
    # h2 has no match → only h1 and h3 paired.
    assert pairs == {0: 0, 2: 1}


def test_align_equal_length_replace_pairs_positionally():
    """When difflib reports a same-size 'replace' block, we still pair to record diffs."""
    a = ["h1", "x1", "h3"]
    b = ["h1", "y1", "h3"]
    pairs = _align_normalized(a, b)
    assert pairs == {0: 0, 1: 1, 2: 2}


def test_align_unequal_replace_leaves_block_unaligned():
    a = ["h1", "x1", "x2", "h4"]
    b = ["h1", "y1", "h4"]
    pairs = _align_normalized(a, b)
    # The middle block has different sizes — leave both sides unaligned, only equal anchors pair.
    assert pairs == {0: 0, 3: 2}


# ---------- process_one (full pipeline) ----------

def _setup_chapter(tmp_path: Path) -> dict:
    """Build a minimal chapter on disk: wikisource source + canonical text + ctext source."""
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}

    # 1. Wikisource source HTML + canonical texts/ md (parse + render via real path)
    ws_dir = tmp_path / "sources" / "wikisource" / "sanguozhi" / "wei"
    ws_dir.mkdir(parents=True)
    ws_html = WS_FIXTURE.read_bytes()
    (ws_dir / "01.html").write_bytes(ws_html)

    ws_chapter = parse_wikisource_html(ws_html.decode("utf-8"))
    md = ws_render_markdown(
        ws_chapter,
        work="sanguozhi", work_title="三國志",
        book="wei", book_title="魏書", work_prefix="wei",
        juan=1, title=ws_chapter.title, author="陳壽",
        source_url="https://zh.wikisource.org/wiki/三國志/卷01",
        source_sha256=hashlib.sha256(ws_html).hexdigest(),
        source_retrieved="2026-05-01",
    )
    text_dir = tmp_path / "texts" / "sanguozhi" / "wei"
    text_dir.mkdir(parents=True)
    (text_dir / "01.md").write_text(md, encoding="utf-8")

    # 2. ctext source — use the synthetic fixture, which has 3 paragraphs aligning
    #    to the wikisource fixture's 3 substantive paragraphs (slightly different
    #    canonical texts, so we can verify diff capture).
    ctext_dir = tmp_path / "sources" / "ctext" / "sanguozhi" / "wei"
    ctext_dir.mkdir(parents=True)
    (ctext_dir / "01.html").write_bytes(CTEXT_FIXTURE.read_bytes())

    return entry


def test_process_one_writes_variants_yaml(tmp_path):
    entry = _setup_chapter(tmp_path)
    result = process_one(
        entry, repo_root=tmp_path, retrieved="2026-05-01",
        fetcher=lambda url: (_ for _ in ()).throw(AssertionError("no network")),
        no_fetch=True,
    )
    assert isinstance(result, VariantResult)
    assert result.variants_path == tmp_path / "variants" / "sanguozhi" / "wei" / "01.yaml"
    assert result.variants_path.exists()
    assert result.n_canonical == 3
    assert result.n_source == 3


def test_variants_yaml_shape(tmp_path):
    entry = _setup_chapter(tmp_path)
    process_one(entry, repo_root=tmp_path, retrieved="2026-05-01",
                fetcher=lambda u: b"", no_fetch=True)
    doc = yaml.safe_load((tmp_path / "variants/sanguozhi/wei/01.yaml").read_text(encoding="utf-8"))
    assert doc["chapter"] == "wei.1"
    assert doc["canonical"] == "wikisource"
    assert "ctext" in doc["sources"]
    assert doc["sources"]["ctext"]["url"] == "https://ctext.org/sanguozhi/1"
    assert doc["alignment"]["method"] == "normalized_hash_diff"
    assert doc["alignment"]["n_canonical_segments"] == 3
    assert isinstance(doc["segments"], dict)


def test_skips_when_no_ctext_html(tmp_path):
    """A chapter without a cached ctext snapshot under --no-fetch is reported as skipped (not failed)."""
    entry = {"ctext_juan": 5, "book": "wei", "juan": 5}
    # Set up only the wikisource side.
    ws_html = WS_FIXTURE.read_bytes()
    ws_dir = tmp_path / "sources" / "wikisource" / "sanguozhi" / "wei"
    ws_dir.mkdir(parents=True)
    (ws_dir / "05.html").write_bytes(ws_html)

    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-01",
                         fetcher=lambda u: b"", no_fetch=True)
    assert isinstance(result, VariantSkip)
    assert "TOC" in result.reason or "no <td" in result.reason


def test_skips_when_ctext_is_a_toc(tmp_path):
    """A TOC ctext page (no <td class=\"ctext\">) is detected and skipped."""
    entry = _setup_chapter(tmp_path)
    # Overwrite the ctext snapshot with an obvious TOC (no body cells).
    toc_path = tmp_path / "sources/ctext/sanguozhi/wei/01.html"
    toc_path.write_bytes(b"<html><body>navigation only</body></html>")
    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-01",
                         fetcher=lambda u: b"", no_fetch=True)
    assert isinstance(result, VariantSkip)
    assert result.reason == SKIP_REASON_TOC


def test_diff_records_only_for_segments_that_actually_differ(tmp_path):
    """Identical canonical+source segments must not appear under doc['segments']."""
    entry = _setup_chapter(tmp_path)

    # Replace the ctext fixture with an HTML whose three segments match the
    # wikisource canonical text exactly — variants/ should then have empty 'segments'.
    ws_chapter = parse_wikisource_html(WS_FIXTURE.read_text(encoding="utf-8"))
    rows = []
    for i, p in enumerate(ws_chapter.paragraphs, start=1):
        rows.append(
            f'<tr><td><a href="sanguozhi/1#n{i}">{i}</a></td>'
            f'<td class="ctext opt">武帝紀:</td>'
            f'<td class="ctext"><div id="comm{i}"></div>{p.main_text}</td></tr>'
        )
    fake_ctext = "<html><body><table>" + "".join(rows) + "</table></body></html>"
    (tmp_path / "sources/ctext/sanguozhi/wei/01.html").write_text(fake_ctext, encoding="utf-8")

    process_one(entry, repo_root=tmp_path, retrieved="2026-05-01",
                fetcher=lambda u: b"", no_fetch=True)
    doc = yaml.safe_load((tmp_path / "variants/sanguozhi/wei/01.yaml").read_text(encoding="utf-8"))
    assert doc["segments"] == {}
    assert doc["alignment"]["n_aligned"] == 3
