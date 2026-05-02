"""Tests for tools/extract_dates.py — temporal annotation extraction + merge."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.extract_dates import (
    build_temporal_annotations,
    merge_temporal_into_file,
    process_one,
)


def _write_text_file(tmp_path: Path, body_segments: list[tuple[str, str]], book: str = "wei", juan: int = 1) -> Path:
    """Build a minimal texts/ md file with the given (seg_id, paragraph) pairs."""
    lines = []
    for seg_id, text in body_segments:
        lines.append(f'<a id="{seg_id}"></a>')
        lines.append(text)
        lines.append("")
    body = "\n".join(lines)
    fm = (
        "---\n"
        "work: sanguozhi\nwork_title: 三國志\n"
        f"book: {book}\nbook_title: 魏書\n"
        f"juan: {juan}\ntitle: 武帝紀\nauthor: 陳壽\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-01'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
    )
    intermediate = fm + body
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(intermediate)
    correct = file_segments_sha256(parsed.segments)
    final = intermediate.replace("PLACEHOLDER", correct)
    p = tmp_path / "texts" / "sanguozhi" / book / f"{juan:02d}.md"
    p.parent.mkdir(parents=True)
    p.write_text(final, encoding="utf-8")
    return p


def _write_existing_annotations(tmp_path: Path, *, chapter: str, pei_anns: list[dict]) -> Path:
    book, _, juan_str = chapter.partition(".")
    juan = int(juan_str)
    p = tmp_path / "annotations" / "sanguozhi" / book / f"{juan:02d}.yaml"
    p.parent.mkdir(parents=True)
    doc = {
        "chapter": chapter,
        "source": {"id": "wikisource", "url": "x", "retrieved": "2026-05-01", "sha256": "a" * 64},
        "annotations": list(pei_anns),
    }
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ---------- build_temporal_annotations ----------

def test_build_temporal_annotations_finds_absolute_dates(tmp_path):
    text_path = _write_text_file(tmp_path, [
        ("wei.1.p1", "光和末，黃巾起。"),
        ("wei.1.p2", "建安五年春正月，太祖討袁紹。"),
        ("wei.1.p3", "建安二十四年，太祖薨。"),
    ])
    anns = build_temporal_annotations(text_path, book="wei")
    assert [a["id"] for a in anns] == ["wei.1.p2.t1", "wei.1.p3.t1"]
    assert anns[0]["year_ad"] == 200
    assert anns[0]["month_ordinal"] == 1
    assert anns[1]["year_ad"] == 219
    assert "month_chinese" not in anns[1]  # no month captured


def test_temporal_id_counts_per_segment(tmp_path):
    text_path = _write_text_file(tmp_path, [
        ("wei.1.p1", "建安五年事，又有建安十年事。"),
    ])
    anns = build_temporal_annotations(text_path, book="wei")
    assert [a["id"] for a in anns] == ["wei.1.p1.t1", "wei.1.p1.t2"]
    assert [a["year_ad"] for a in anns] == [200, 205]


# ---------- merge_temporal_into_file ----------

def test_merge_preserves_existing_pei_and_replaces_temporal(tmp_path):
    yaml_path = _write_existing_annotations(tmp_path, chapter="wei.1", pei_anns=[
        {"id": "wei.1.p1.a1", "anchor": "wei.1.p1", "at": 0, "length": 0,
         "type": "pei", "text": "《魏書》曰：…"},
        {"id": "wei.1.p1.t1", "anchor": "wei.1.p1", "at": 5, "length": 4,
         "type": "temporal", "text": "STALE", "era": "建安", "era_year": 1, "year_ad": 196},
    ])
    new_temporals = [
        {"id": "wei.1.p2.t1", "anchor": "wei.1.p2", "at": 0, "length": 4,
         "type": "temporal", "text": "建安五年", "era": "建安", "era_year": 5, "year_ad": 200},
    ]
    n = merge_temporal_into_file(yaml_path, new_temporals)
    assert n == 1
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    types_and_ids = [(a["type"], a["id"]) for a in doc["annotations"]]
    # Existing pei stays; stale temporal dropped; new temporal added.
    assert ("pei", "wei.1.p1.a1") in types_and_ids
    assert ("temporal", "wei.1.p2.t1") in types_and_ids
    assert ("temporal", "wei.1.p1.t1") not in types_and_ids


def test_merge_orders_by_anchor_then_at(tmp_path):
    yaml_path = _write_existing_annotations(tmp_path, chapter="wei.1", pei_anns=[
        {"id": "wei.1.p3.a1", "anchor": "wei.1.p3", "at": 5, "length": 0,
         "type": "pei", "text": "..."},
    ])
    merge_temporal_into_file(yaml_path, [
        {"id": "wei.1.p2.t1", "anchor": "wei.1.p2", "at": 0, "length": 4,
         "type": "temporal", "text": "x", "era": "建安", "era_year": 5, "year_ad": 200},
        {"id": "wei.1.p1.t1", "anchor": "wei.1.p1", "at": 0, "length": 4,
         "type": "temporal", "text": "y", "era": "建安", "era_year": 1, "year_ad": 196},
    ])
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    anchors = [a["anchor"] for a in doc["annotations"]]
    assert anchors == ["wei.1.p1", "wei.1.p2", "wei.1.p3"]


def test_merge_errors_when_annotations_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="run.*tools.extract_annotations"):
        merge_temporal_into_file(tmp_path / "nope.yaml", [])


def test_merge_is_idempotent(tmp_path):
    yaml_path = _write_existing_annotations(tmp_path, chapter="wei.1", pei_anns=[
        {"id": "wei.1.p1.a1", "anchor": "wei.1.p1", "at": 0, "length": 0,
         "type": "pei", "text": "x"},
    ])
    new_temporals = [
        {"id": "wei.1.p2.t1", "anchor": "wei.1.p2", "at": 0, "length": 4,
         "type": "temporal", "text": "建安五年", "era": "建安", "era_year": 5, "year_ad": 200},
    ]
    merge_temporal_into_file(yaml_path, new_temporals)
    first = yaml_path.read_bytes()
    merge_temporal_into_file(yaml_path, new_temporals)
    second = yaml_path.read_bytes()
    assert first == second


# ---------- process_one (full pipeline) ----------

def test_process_one_writes_temporal_via_full_pipeline(tmp_path):
    _write_text_file(tmp_path, [
        ("wei.1.p1", "建安五年春正月，太祖討袁紹。"),
    ])
    _write_existing_annotations(tmp_path, chapter="wei.1", pei_anns=[])
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    result = process_one(entry, repo_root=tmp_path)
    assert result.n_temporal == 1
    doc = yaml.safe_load(result.annotations_path.read_text(encoding="utf-8"))
    assert doc["annotations"][0]["type"] == "temporal"
    assert doc["annotations"][0]["year_ad"] == 200


def test_process_one_errors_when_text_missing(tmp_path):
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    with pytest.raises(FileNotFoundError, match="run.*tools.batch_fetch"):
        process_one(entry, repo_root=tmp_path)
