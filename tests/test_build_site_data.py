"""Tests for tools/build_site_data.py — JSON layout for the static site."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.batch_fetch import (
    BOOK_TITLES as SANGUOZHI_BOOK_TITLES,
    derive_paths as derive_paths_sanguozhi,
    load_config as load_config_sanguozhi,
)
from tools.batch_fetch_hhs import BOOK as HHS_BOOK, BOOK_TITLE as HHS_BOOK_TITLE
from tools.batch_fetch_hhs import derive_paths as derive_paths_hhs
from tools.batch_fetch_hhs import load_config as load_config_hhs
from tools.build_site_data import WorkSpec, build_all, build_chapter_dict
from tools.extract_annotations import annotations_path as ann_path_sanguozhi
from tools.extract_annotations_hhs import annotations_path as ann_path_hhs


def _make_sanguozhi_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal sanguozhi state and a tmp config pointing at it.

    Returns (repo_root, config_path)."""
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
             "type": "temporal", "kind": "absolute", "resolution": "absolute",
             "text": "建安五年春正月",
             "era": "建安", "era_year": 5, "year_ad": 200,
             "month_chinese": "春正月", "month_ordinal": 1},
        ],
    }
    ann_p = tmp_path / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    ann_p.parent.mkdir(parents=True)
    ann_p.write_text(yaml.safe_dump(ann, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")

    cfg_path = tmp_path / "sanguozhi.yaml"
    cfg_path.write_text("- { ctext_juan: 1, book: wei, juan: 1 }\n", encoding="utf-8")
    return tmp_path, cfg_path


def _sanguozhi_workspec(cfg_path: Path) -> WorkSpec:
    return WorkSpec(
        work_id="sanguozhi", work_title="三國志",
        config_path=cfg_path,
        load_config=load_config_sanguozhi,
        derive_paths=derive_paths_sanguozhi,
        annotations_path=ann_path_sanguozhi,
        book_titles=SANGUOZHI_BOOK_TITLES,
        book_order=["wei", "shu", "wu"],
    )


def _hhs_workspec(cfg_path: Path) -> WorkSpec:
    return WorkSpec(
        work_id="houhanshu", work_title="後漢書",
        config_path=cfg_path,
        load_config=load_config_hhs,
        derive_paths=derive_paths_hhs,
        annotations_path=ann_path_hhs,
        book_titles={HHS_BOOK: HHS_BOOK_TITLE},
        book_order=[HHS_BOOK],
        book_for_entry=lambda e: HHS_BOOK,
    )


# ---------- build_chapter_dict ----------

def test_chapter_dict_top_fields(tmp_path):
    repo, cfg = _make_sanguozhi_repo(tmp_path)
    work = _sanguozhi_workspec(cfg)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, work=work, repo_root=repo)
    assert ch["id"] == "wei.1"
    assert ch["work"] == "sanguozhi"
    assert ch["book"] == "wei"
    assert ch["title"] == "武帝紀"
    assert ch["author"] == "陳壽"
    assert ch["n_segments"] == 2


def test_chapter_dict_groups_annotations_by_anchor(tmp_path):
    repo, cfg = _make_sanguozhi_repo(tmp_path)
    work = _sanguozhi_workspec(cfg)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, work=work, repo_root=repo)
    p1 = ch["segments"][0]
    p2 = ch["segments"][1]
    assert p1["id"] == "wei.1.p1"
    assert len(p1["annotations"]) == 2
    types = {a["type"] for a in p1["annotations"]}
    assert types == {"pei", "temporal"}
    assert p2["annotations"] == []


def test_temporal_annotation_carries_year_ad(tmp_path):
    repo, cfg = _make_sanguozhi_repo(tmp_path)
    work = _sanguozhi_workspec(cfg)
    entry = {"ctext_juan": 1, "book": "wei", "juan": 1}
    ch = build_chapter_dict(entry, work=work, repo_root=repo)
    temporals = [a for a in ch["segments"][0]["annotations"] if a["type"] == "temporal"]
    assert len(temporals) == 1
    t = temporals[0]
    assert t["year_ad"] == 200
    assert t["era"] == "建安"
    assert t["era_year"] == 5
    assert t["month_ordinal"] == 1
    assert t["kind"] == "absolute"
    assert t["resolution"]


# ---------- build_all (full pipeline + index.json shape) ----------

def test_build_all_writes_per_chapter_files_and_index(tmp_path):
    repo, cfg = _make_sanguozhi_repo(tmp_path)
    work = _sanguozhi_workspec(cfg)
    results = build_all(repo_root=repo, works=[work])
    assert len(results) == 1
    out = repo / "site" / "data"
    assert (out / "sanguozhi" / "wei" / "01.json").exists()
    assert (out / "index.json").exists()

    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    # books carry work_id and a chapter list with data_url for direct fetching.
    wei = next(b for b in idx["books"] if b["id"] == "wei")
    assert wei["work_id"] == "sanguozhi"
    assert wei["chapters"][0]["data_url"] == "data/sanguozhi/wei/01.json"


def test_build_all_is_idempotent(tmp_path):
    repo, cfg = _make_sanguozhi_repo(tmp_path)
    work = _sanguozhi_workspec(cfg)
    build_all(repo_root=repo, works=[work])
    a = (repo / "site" / "data" / "sanguozhi" / "wei" / "01.json").read_bytes()
    b_idx = (repo / "site" / "data" / "index.json").read_bytes()
    build_all(repo_root=repo, works=[work])
    assert (repo / "site" / "data" / "sanguozhi" / "wei" / "01.json").read_bytes() == a
    assert (repo / "site" / "data" / "index.json").read_bytes() == b_idx


def test_build_all_combines_multiple_works_into_one_index(tmp_path):
    """When sanguozhi + 后汉书 are both processed, books from both appear in the same index."""
    repo, sanguozhi_cfg = _make_sanguozhi_repo(tmp_path)
    # Stub a tiny hhs chapter
    hhs_text_path = repo / "texts" / "houhanshu" / "08.md"
    hhs_text_path.parent.mkdir(parents=True)
    text = (
        "---\nwork: houhanshu\nwork_title: 後漢書\nbook: hhs\nbook_title: 後漢書\n"
        "juan: 8\ntitle: 孝靈帝紀\nauthor: 范曄\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-02'\n"
        f"  sha256: {'b' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="hhs.8.p1"></a>\n孝靈皇帝諱宏。\n'
    )
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    text = text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))
    hhs_text_path.write_text(text, encoding="utf-8")
    hhs_cfg = repo / "hhs.yaml"
    hhs_cfg.write_text("- { juan: 8, category: benji }\n", encoding="utf-8")

    works = [_sanguozhi_workspec(sanguozhi_cfg), _hhs_workspec(hhs_cfg)]
    build_all(repo_root=repo, works=works)
    idx = json.loads((repo / "site" / "data" / "index.json").read_text(encoding="utf-8"))
    book_ids = [b["id"] for b in idx["books"]]
    assert "wei" in book_ids
    assert "hhs" in book_ids
    hhs = next(b for b in idx["books"] if b["id"] == "hhs")
    assert hhs["work_id"] == "houhanshu"
    assert hhs["chapters"][0]["data_url"] == "data/houhanshu/hhs/08.json"
