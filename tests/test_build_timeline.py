"""Tests for tools/build_timeline.py — cross-source timeline JSON."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.build_timeline import (
    _int_to_chinese,
    _make_snippet,
    build_timeline_dict,
    collect_refs,
    write_timeline,
)


# ---------- _int_to_chinese ----------

@pytest.mark.parametrize("n,expected", [
    (1, "一"), (5, "五"), (9, "九"), (10, "十"),
    (11, "十一"), (15, "十五"), (19, "十九"),
    (20, "二十"), (24, "二十四"), (30, "三十"), (99, "九十九"),
])
def test_int_to_chinese(n, expected):
    assert _int_to_chinese(n) == expected


# ---------- _make_snippet ----------

def test_snippet_centers_on_anchor():
    """Snippet shows `before` chars before the anchor and `after` chars after."""
    text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"  # 22 chars (0=甲 … 10=子 … 21=亥)
    snip = _make_snippet(text, at=10, before=3, after=4)
    # start = 10-3=7 (辛); end = 10+4=14 (excl., last kept = 卯). text[7:14] = "辛壬癸子丑寅卯".
    assert snip == "…辛壬癸子丑寅卯…"


def test_snippet_no_ellipsis_at_text_boundary():
    text = "短"
    assert _make_snippet(text, at=0, before=10, after=10) == "短"


# ---------- collect_refs (full pipeline against a tmp repo) ----------

def _make_tmp_repo(tmp_path: Path) -> Path:
    """Build a minimal repo with one sanguozhi chapter + temporal annotations."""
    text = (
        "---\n"
        "work: sanguozhi\nwork_title: 三國志\n"
        "book: wei\nbook_title: 魏書\n"
        "juan: 1\ntitle: 武帝紀\nauthor: 陳壽\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="wei.1.p1"></a>\n'
        "中平六年冬十二月，太祖起兵於己吾。\n\n"
        '<a id="wei.1.p2"></a>\n'
        "初平元年春正月，諸軍同時俱起兵。\n"
    )
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    text = text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))
    text_path = tmp_path / "texts" / "sanguozhi" / "wei" / "01.md"
    text_path.parent.mkdir(parents=True)
    text_path.write_text(text, encoding="utf-8")

    ann = {
        "chapter": "wei.1",
        "source": {"id": "wikisource", "url": "x", "retrieved": "2026-05-03",
                   "sha256": "a" * 64},
        "annotations": [
            {"id": "wei.1.p1.t1", "anchor": "wei.1.p1", "at": 0, "length": 4,
             "type": "temporal", "kind": "absolute", "resolution": "absolute",
             "text": "中平六年", "era": "中平", "era_year": 6, "year_ad": 189},
            {"id": "wei.1.p2.t1", "anchor": "wei.1.p2", "at": 0, "length": 7,
             "type": "temporal", "kind": "absolute", "resolution": "absolute",
             "text": "初平元年春正月", "era": "初平", "era_year": 1, "year_ad": 190,
             "month_chinese": "春正月", "month_ordinal": 1},
        ],
    }
    ann_path = tmp_path / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    ann_path.parent.mkdir(parents=True)
    ann_path.write_text(yaml.safe_dump(ann, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    return tmp_path


def test_collect_refs_walks_annotations(tmp_path):
    repo = _make_tmp_repo(tmp_path)
    refs = collect_refs(repo_root=repo)
    assert len(refs) == 2
    by_year = {r.year_ad: r for r in refs}
    assert 189 in by_year and 190 in by_year
    r190 = by_year[190]
    assert r190.surface == "初平元年春正月"
    assert r190.month_ordinal == 1
    assert r190.chapter_title == "武帝紀"


def test_collect_refs_skips_non_temporal_annotations(tmp_path):
    repo = _make_tmp_repo(tmp_path)
    # Add a pei annotation alongside the temporal ones.
    ann_path = repo / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"].append({
        "id": "wei.1.p1.a1", "anchor": "wei.1.p1", "at": 4, "length": 0,
        "type": "pei", "text": "《魏書》曰：…",
    })
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    refs = collect_refs(repo_root=repo)
    assert all(r.kind in ("absolute", "relative") for r in refs)
    assert len(refs) == 2  # 裴注 not in refs


# ---------- build_timeline_dict ----------

def test_timeline_groups_by_year_and_orders_events(tmp_path):
    repo = _make_tmp_repo(tmp_path)
    refs = collect_refs(repo_root=repo)
    tl = build_timeline_dict(refs)
    assert [y["year_ad"] for y in tl["years"]] == [189, 190]
    yr190 = tl["years"][1]
    assert yr190["n_events"] == 1
    assert yr190["events"][0]["surface"] == "初平元年春正月"
    assert yr190["events"][0]["data_url"] == "data/sanguozhi/wei/01.json"


def test_timeline_year_labels_distinct_and_sorted(tmp_path):
    repo = _make_tmp_repo(tmp_path)
    # Add two more temporal anns mapping to AD 189 from different eras (e.g.
    # 光熹元年, 永漢元年 also resolve to 189; same year, multiple labels).
    ann_path = repo / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"].extend([
        {"id": "wei.1.p1.t2", "anchor": "wei.1.p1", "at": 4, "length": 4,
         "type": "temporal", "kind": "absolute", "resolution": "absolute",
         "text": "光熹元年", "era": "光熹", "era_year": 1, "year_ad": 189},
        {"id": "wei.1.p1.t3", "anchor": "wei.1.p1", "at": 8, "length": 4,
         "type": "temporal", "kind": "absolute", "resolution": "absolute",
         "text": "中平六年", "era": "中平", "era_year": 6, "year_ad": 189},  # duplicate label, dedup
    ])
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    refs = collect_refs(repo_root=repo)
    tl = build_timeline_dict(refs)
    yr189 = next(y for y in tl["years"] if y["year_ad"] == 189)
    labels = [(l["era"], l["era_year"]) for l in yr189["labels"]]
    assert ("中平", 6) in labels
    assert ("光熹", 1) in labels
    assert len(labels) == 2  # deduplicated


def test_write_timeline_emits_json(tmp_path):
    repo = _make_tmp_repo(tmp_path)
    out = write_timeline(repo_root=repo)
    assert out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["generated_by"] == "tools/build_timeline.py"
    assert any(y["year_ad"] == 189 for y in doc["years"])


def test_houhanshu_chapter_path_resolves(tmp_path):
    """A 后汉书 annotations file should be picked up at houhanshu/<NN>.yaml (no book sub-dir)."""
    # Build a tiny hhs chapter
    text = (
        "---\nwork: houhanshu\nwork_title: 後漢書\nbook: hhs\nbook_title: 後漢書\n"
        "juan: 8\ntitle: 孝靈帝紀\nauthor: 范曄\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'b' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="hhs.8.p1"></a>\n中平元年春二月，鉅鹿人張角自稱黃天。\n'
    )
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    text = text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))
    p = tmp_path / "texts" / "houhanshu" / "08.md"
    p.parent.mkdir(parents=True)
    p.write_text(text, encoding="utf-8")

    ann = {
        "chapter": "hhs.8",
        "source": {"id": "wikisource", "url": "x", "retrieved": "2026-05-03",
                   "sha256": "b" * 64},
        "annotations": [
            {"id": "hhs.8.p1.t1", "anchor": "hhs.8.p1", "at": 0, "length": 6,
             "type": "temporal", "kind": "absolute", "resolution": "absolute",
             "text": "中平元年春二月", "era": "中平", "era_year": 1, "year_ad": 184,
             "month_chinese": "春二月", "month_ordinal": 2},
        ],
    }
    ap = tmp_path / "annotations" / "houhanshu" / "08.yaml"
    ap.parent.mkdir(parents=True)
    ap.write_text(yaml.safe_dump(ann, allow_unicode=True, sort_keys=False), encoding="utf-8")

    refs = collect_refs(repo_root=tmp_path)
    assert len(refs) == 1
    r = refs[0]
    assert r.work == "houhanshu"
    assert r.year_ad == 184
    assert r.chapter_title == "孝靈帝紀"
    # data_url under the houhanshu/hhs/ tree.
    tl = build_timeline_dict(refs)
    assert tl["years"][0]["events"][0]["data_url"] == "data/houhanshu/hhs/08.json"
