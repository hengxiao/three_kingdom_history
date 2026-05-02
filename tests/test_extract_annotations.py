"""Tests for tools/extract_annotations.py — uses Wikisource fixture, no network."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from tools.extract_annotations import (
    annotations_path,
    process_one,
    render_annotations_yaml,
    run,
)
from tools.fetch_wikisource import parse_wikisource_html

FIXTURE = Path(__file__).parent / "fixtures" / "wikisource_sample.html"


# ---------- annotations_path ----------

def test_annotations_path_mirrors_text_layout(tmp_path):
    p = annotations_path({"book": "wei", "juan": 5}, repo_root=tmp_path)
    assert p == tmp_path / "annotations" / "sanguozhi" / "wei" / "05.yaml"


def test_annotations_path_two_digit_padding():
    p = annotations_path({"book": "wu", "juan": 7}, repo_root=Path("/r"))
    assert p == Path("/r/annotations/sanguozhi/wu/07.yaml")


# ---------- render_annotations_yaml ----------

@pytest.fixture(scope="module")
def chapter():
    return parse_wikisource_html(FIXTURE.read_text(encoding="utf-8"))


def test_renders_one_yaml_doc_with_chapter_and_source(chapter):
    text = render_annotations_yaml(
        chapter,
        chapter_id="wei.5",
        work_prefix="wei",
        juan=5,
        source_url="https://zh.wikisource.org/wiki/三國志/卷05",
        source_sha256="a" * 64,
        source_retrieved="2026-05-01",
    )
    doc = yaml.safe_load(text)
    assert doc["chapter"] == "wei.5"
    assert doc["source"]["id"] == "wikisource"
    assert doc["source"]["sha256"] == "a" * 64


def test_annotation_ids_use_segment_id_prefix(chapter):
    """Annotation IDs follow <segment-id>.aN; aN counts from 1 within each paragraph."""
    text = render_annotations_yaml(
        chapter, chapter_id="wei.5", work_prefix="wei", juan=5,
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    doc = yaml.safe_load(text)
    # Fixture: p1 has 0 annotations; p2 has 1; p3 has 2.
    expected = ["wei.5.p2.a1", "wei.5.p3.a1", "wei.5.p3.a2"]
    assert [a["id"] for a in doc["annotations"]] == expected


def test_each_annotation_has_required_fields(chapter):
    text = render_annotations_yaml(
        chapter, chapter_id="wei.5", work_prefix="wei", juan=5,
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    doc = yaml.safe_load(text)
    for a in doc["annotations"]:
        assert set(a) == {"id", "anchor", "at", "length", "type", "text"}
        assert a["type"] == "pei"
        assert a["length"] == 0
        assert isinstance(a["at"], int)
        assert isinstance(a["text"], str) and a["text"]


def test_yaml_is_deterministic(chapter):
    """Re-rendering the same chapter must produce byte-identical YAML."""
    args = dict(
        chapter_id="wei.5", work_prefix="wei", juan=5,
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    a = render_annotations_yaml(chapter, **args)
    b = render_annotations_yaml(chapter, **args)
    assert a == b


def test_parse_warnings_propagate_when_present():
    """Chapters with stray brackets carry warnings into the YAML for downstream audit."""
    bad_html = (
        '<table class="ws-header"><tr><td align="center">'
        '<b><a>三國志</a></b><br />魏書·武帝紀</td></tr></table>'
        '<div class="mw-parser-output">'
        '<p>第一段〈正常注〉常文。</p>'
        '<p>第二段有不匹配的〈開括號但沒對應再多湊夠最少漢字。</p>'
        '</div>'
    )
    ch = parse_wikisource_html(bad_html)
    text = render_annotations_yaml(
        ch, chapter_id="wei.1", work_prefix="wei", juan=1,
        source_url="x", source_sha256="x" * 64, source_retrieved="2026-05-01",
    )
    doc = yaml.safe_load(text)
    assert doc.get("parse_warnings"), "expected parse_warnings to be set"
    assert any("stray" in w for w in doc["parse_warnings"])


# ---------- process_one ----------

def test_process_one_writes_file(tmp_path):
    src = tmp_path / "sources" / "wikisource" / "sanguozhi" / "wei" / "05.html"
    src.parent.mkdir(parents=True)
    src.write_bytes(FIXTURE.read_bytes())

    entry = {"ctext_juan": 5, "book": "wei", "juan": 5}
    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-01")
    assert result.chapter_id == "wei.5"
    assert result.annotations_path.exists()
    assert result.n_annotations == 3  # fixture has 0 + 1 + 2 = 3 annotations

    doc = yaml.safe_load(result.annotations_path.read_text(encoding="utf-8"))
    expected_sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert doc["source"]["sha256"] == expected_sha
    assert len(doc["annotations"]) == 3


def test_process_one_errors_when_source_missing(tmp_path):
    entry = {"ctext_juan": 5, "book": "wei", "juan": 5}
    with pytest.raises(FileNotFoundError, match="run `tools.batch_fetch`"):
        process_one(entry, repo_root=tmp_path, retrieved="2026-05-01")


# ---------- run() driver ----------

def test_run_processes_each_entry(tmp_path):
    cfg = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 5, "book": "wei", "juan": 5},
    ]
    for e in cfg:
        d = tmp_path / "sources" / "wikisource" / "sanguozhi" / e["book"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{int(e['juan']):02d}.html").write_bytes(FIXTURE.read_bytes())

    results = list(run(cfg, repo_root=tmp_path, retrieved="2026-05-01"))
    from tools.extract_annotations import AnnotationsResult
    assert all(isinstance(r, AnnotationsResult) for r in results)
    for r in results:
        assert r.annotations_path.exists()


def test_run_only_filter(tmp_path):
    cfg = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 31, "book": "shu", "juan": 1},
    ]
    src = tmp_path / "sources" / "wikisource" / "sanguozhi" / "shu" / "01.html"
    src.parent.mkdir(parents=True)
    src.write_bytes(FIXTURE.read_bytes())

    results = list(run(cfg, repo_root=tmp_path, only={31}, retrieved="2026-05-01"))
    assert len(results) == 1
    from tools.extract_annotations import AnnotationsResult
    assert isinstance(results[0], AnnotationsResult)
    assert results[0].chapter_id == "shu.1"
