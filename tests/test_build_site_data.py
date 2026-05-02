"""Tests for tools/build_site_data.py — JSON layout for the static site."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.build_site_data import build_all, build_chapter_dict, build_index_dict


def _make_repo(tmp_path: Path) -> Path:
    """Build a minimal repo state: one chapter, two segments, two annotations."""
    text = (
        "---\n"
        "work: sanguozhi\nwork_title: 三國志\n"
        "book: wei\nbook_title: 魏書\n"
        "juan: 1\ntitle: 武帝紀\nauthor: 陳壽\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: https://example/test\n"
        "  retrieved: '2026-05-01'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="wei.1.p1"></a>\n'
        "建安五年春正月，太祖討袁紹。\n\n"
        '<a id="wei.1.p2"></a>\n'
        "二月，太祖大破紹於官渡。\n"
    )
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    text = text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))
    text_path = tmp_path / "texts" / "sanguozhi" / "wei" / "01.md"
    text_path.parent.mkdir(parents=True)
    text_path.write_text(text, encoding="utf-8")

    ann = {
        "chapter": "wei.1",
        "source": {"id": "wikisource", "url": "x", "retrieved": "2026-05-01",
                   "sha256": "a" * 64},
        "annotations": [
            {"id": "wei.1.p1.a1", "anchor": "wei.1.p1", "at": 5, "length": 0,
             "type": "pei", "text": "《魏書》曰：太祖將戰。"},
            {"id": "wei.1.p1.t1", "anchor": "wei.1.p1", "at": 0, "length": 7,
             "type": "temporal", "text": "建安五年春正月",
             "era": "建安", "era_year": 5, "year_ad": 200,
             "month_chinese": "春正月", "month_ordinal": 1},
        ],
    }
    ann_path = tmp_path / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    ann_path.parent.mkdir(parents=True)
    ann_path.write_text(yaml.safe_dump(ann, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    return tmp_path


# ---------- build_chapter_dict ----------

def test_chapter_dict_top_fields(tmp_path):
    repo = _make_repo(tmp_path)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, repo_root=repo)
    assert ch["id"] == "wei.1"
    assert ch["title"] == "武帝紀"
    assert ch["author"] == "陳壽"
    assert ch["source"]["id"] == "wikisource"
    assert ch["n_segments"] == 2


def test_chapter_dict_groups_annotations_by_anchor(tmp_path):
    repo = _make_repo(tmp_path)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, repo_root=repo)
    p1 = ch["segments"][0]
    p2 = ch["segments"][1]
    assert p1["id"] == "wei.1.p1"
    assert len(p1["annotations"]) == 2
    types = {a["type"] for a in p1["annotations"]}
    assert types == {"pei", "temporal"}
    assert p2["annotations"] == []


def test_temporal_annotation_carries_year_ad(tmp_path):
    repo = _make_repo(tmp_path)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, repo_root=repo)
    temporals = [a for a in ch["segments"][0]["annotations"] if a["type"] == "temporal"]
    assert len(temporals) == 1
    t = temporals[0]
    assert t["year_ad"] == 200
    assert t["era"] == "建安"
    assert t["era_year"] == 5
    assert t["month_ordinal"] == 1


def test_pei_annotation_carries_text_and_position(tmp_path):
    repo = _make_repo(tmp_path)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, repo_root=repo)
    peis = [a for a in ch["segments"][0]["annotations"] if a["type"] == "pei"]
    assert peis[0]["text"] == "《魏書》曰：太祖將戰。"
    assert peis[0]["at"] == 5
    assert peis[0]["length"] == 0


# ---------- build_index_dict ----------

def test_index_orders_books_wei_shu_wu(tmp_path):
    repo = _make_repo(tmp_path)
    cfg = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
    ]
    idx = build_index_dict(cfg, repo_root=repo)
    assert idx["work"] == "sanguozhi"
    assert [b["id"] for b in idx["books"]] == ["wei"]
    assert idx["books"][0]["title"] == "魏書"
    assert idx["books"][0]["chapters"][0]["title"] == "武帝紀"
    assert idx["books"][0]["chapters"][0]["n_pei"] == 1
    assert idx["books"][0]["chapters"][0]["n_temporal"] == 1


def test_index_skips_chapters_without_text_file(tmp_path):
    repo = _make_repo(tmp_path)
    cfg = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 5, "book": "wei", "juan": 5},  # no text file
    ]
    idx = build_index_dict(cfg, repo_root=repo)
    assert len(idx["books"][0]["chapters"]) == 1


# ---------- build_all (full pipeline) ----------

def test_build_all_writes_index_and_chapter_files(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    config_path = tmp_path / "chapters.yaml"
    config_path.write_text("- { ctext_juan: 1, book: wei, juan: 1 }\n", encoding="utf-8")

    results = build_all(repo_root=repo, config_path=config_path)
    assert len(results) == 1
    out = repo / "site" / "data"
    assert (out / "index.json").exists()
    assert (out / "sanguozhi" / "wei" / "01.json").exists()

    # JSON loads back identical via json.
    ch = json.loads((out / "sanguozhi" / "wei" / "01.json").read_text(encoding="utf-8"))
    assert ch["id"] == "wei.1"
    assert ch["segments"][0]["text"] == "建安五年春正月，太祖討袁紹。"

    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert idx["books"][0]["chapters"][0]["title"] == "武帝紀"


def test_build_all_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    config_path = tmp_path / "chapters.yaml"
    config_path.write_text("- { ctext_juan: 1, book: wei, juan: 1 }\n", encoding="utf-8")

    build_all(repo_root=repo, config_path=config_path)
    a = (repo / "site" / "data" / "sanguozhi" / "wei" / "01.json").read_bytes()
    build_all(repo_root=repo, config_path=config_path)
    b = (repo / "site" / "data" / "sanguozhi" / "wei" / "01.json").read_bytes()
    assert a == b
