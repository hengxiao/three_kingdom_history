"""Tests for tools/build_people.py — synthetic chapters + 2-person config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.build_people import (
    build_index_dict,
    build_one_person,
    find_mentions_for_person,
    load_all_chapters,
    load_config,
    write_all,
)


def _stamp_segments_sha256(text: str) -> str:
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    return text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))


def _seed_repo(tmp_path: Path) -> Path:
    """One sanguozhi chapter + one zztj chapter, mentioning two people in different mixes.

    Also seeds the corresponding annotations YAML files with the person
    annotations we'd expect from `tools.extract_persons` — build_people now
    reads those annotations rather than running its own substring search.
    """
    sg_text = (
        "---\n"
        "work: sanguozhi\nwork_title: 三國志\nbook: wei\nbook_title: 魏書\n"
        "juan: 1\ntitle: 武帝紀\nauthor: 陳壽\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="wei.1.p1"></a>\n太祖武皇帝，姓曹，諱操，字孟德，沛國譙人。\n\n'
        '<a id="wei.1.p2"></a>\n董卓進京，太祖拒之，乃變易姓名而東歸。\n\n'
        '<a id="wei.1.p3"></a>\n與袁紹會盟。\n'
    )
    sg_text = _stamp_segments_sha256(sg_text)
    sg_path = tmp_path / "texts" / "sanguozhi" / "wei" / "01.md"
    sg_path.parent.mkdir(parents=True)
    sg_path.write_text(sg_text, encoding="utf-8")

    zz_text = (
        "---\n"
        "work: zztj\nwork_title: 資治通鑑\nbook: zztj\nbook_title: 資治通鑑\n"
        "juan: 60\ntitle: 漢紀五十二\nauthor: 司馬光\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: y\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'b' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="zztj.60.p1"></a>\n中平六年，董卓擅朝政。\n\n'
        '<a id="zztj.60.p2"></a>\n初平元年春正月，袁紹為盟主，曹操行奮武將軍。\n\n'
        '<a id="zztj.60.p3"></a>\n紙上談兵，無關之段。\n'
    )
    zz_text = _stamp_segments_sha256(zz_text)
    zz_path = tmp_path / "texts" / "zztj" / "060.md"
    zz_path.parent.mkdir(parents=True)
    zz_path.write_text(zz_text, encoding="utf-8")

    # Seed annotation YAMLs to mimic what extract_persons would produce.
    sg_ann_path = tmp_path / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    sg_ann_path.parent.mkdir(parents=True)
    sg_ann_path.write_text(yaml.safe_dump({
        "chapter": "wei.1",
        "annotations": [
            # wei.1.p1 mentions 曹操 (alias 孟德 also present in '字孟德').
            {"id": "wei.1.p1.h1", "anchor": "wei.1.p1", "at": 8, "length": 1,
             "type": "person", "person_id": "caocao", "text": "操", "via": "given_name"},
            # 'wei.1.p2 mentions 董卓 at offset 0.
            {"id": "wei.1.p2.h1", "anchor": "wei.1.p2", "at": 0, "length": 2,
             "type": "person", "person_id": "dongzhuo", "text": "董卓", "via": "primary"},
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    zz_ann_path = tmp_path / "annotations" / "zztj" / "060.yaml"
    zz_ann_path.parent.mkdir(parents=True)
    zz_ann_path.write_text(yaml.safe_dump({
        "chapter": "zztj.60",
        "annotations": [
            # zztj.60.p1 mentions 董卓 at offset 5.
            {"id": "zztj.60.p1.h1", "anchor": "zztj.60.p1", "at": 5, "length": 2,
             "type": "person", "person_id": "dongzhuo", "text": "董卓", "via": "primary"},
            # zztj.60.p2 mentions 曹操 at offset 11.
            {"id": "zztj.60.p2.h1", "anchor": "zztj.60.p2", "at": 11, "length": 2,
             "type": "person", "person_id": "caocao", "text": "曹操", "via": "primary"},
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_path


def _config_path(tmp_path: Path) -> Path:
    cfg = [
        {"id": "caocao", "primary_name": "曹操", "courtesy_name": "孟德",
         "brief": "魏太祖", "bio_chapters": ["wei.1"]},
        {"id": "dongzhuo", "primary_name": "董卓", "courtesy_name": "仲穎",
         "brief": "漢末權臣", "bio_chapters": []},
    ]
    p = tmp_path / "people.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ---------- load_config ----------

def test_load_config_rejects_duplicate_ids(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- { id: x, primary_name: A }\n- { id: x, primary_name: B }\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(bad)


def test_load_config_rejects_missing_fields(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- { id: x }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="primary_name"):
        load_config(bad)


# ---------- load_all_chapters ----------

def test_load_all_chapters_walks_three_works(tmp_path):
    repo = _seed_repo(tmp_path)
    chapters = load_all_chapters(repo)
    assert "wei.1" in chapters
    assert "zztj.60" in chapters
    assert chapters["wei.1"].chapter_title == "武帝紀"
    assert chapters["zztj.60"].book_title == "資治通鑑"


# ---------- find_mentions_for_person ----------

def test_find_mentions_skips_bio_chapter(tmp_path):
    repo = _seed_repo(tmp_path)
    chapters = load_all_chapters(repo)
    person = {"id": "caocao", "primary_name": "曹操",
              "bio_chapters": ["wei.1"], "aliases": []}
    mentions = find_mentions_for_person(person, chapters, skip_chapter_ids={"wei.1"})
    # Only zztj.60.p2 mentions 曹操 outside the bio.
    assert len(mentions) == 1
    m = mentions[0]
    assert m["chapter_id"] == "zztj.60"
    assert m["anchor"] == "zztj.60.p2"
    assert m["matched"] == "曹操"


def test_find_mentions_aliases_are_searched(tmp_path):
    repo = _seed_repo(tmp_path)
    chapters = load_all_chapters(repo)
    person = {"id": "caocao", "primary_name": "曹操",
              "bio_chapters": [], "aliases": ["孟德"]}
    mentions = find_mentions_for_person(person, chapters, skip_chapter_ids=set())
    matches = {m["matched"] for m in mentions}
    # bio chapter wei.1.p1 has 字孟德 — alias should hit it; primary_name should hit zztj.60.p2.
    assert "曹操" in matches or "孟德" in matches


def test_find_mentions_dedups_per_segment(tmp_path):
    """If a segment matches both primary_name and an alias, record one mention with the longest match."""
    repo = _seed_repo(tmp_path)
    # Alter wei.1.p1 to mention 曹操 + 孟德 in same segment (already does).
    chapters = load_all_chapters(repo)
    person = {"id": "caocao", "primary_name": "曹操",
              "bio_chapters": [], "aliases": ["孟德"]}
    mentions = find_mentions_for_person(person, chapters, skip_chapter_ids=set())
    # Each (chapter, segment) pair appears at most once.
    keys = [(m["chapter_id"], m["anchor"]) for m in mentions]
    assert len(keys) == len(set(keys))


# ---------- build_one_person ----------

def test_build_one_person_groups_by_work(tmp_path):
    repo = _seed_repo(tmp_path)
    chapters = load_all_chapters(repo)
    person = {"id": "dongzhuo", "primary_name": "董卓",
              "courtesy_name": "仲穎", "brief": "漢末權臣",
              "bio_chapters": []}
    out = build_one_person(person, chapters)
    assert out["primary_name"] == "董卓"
    # 董卓 mentioned in wei.1.p2 (sanguozhi) and zztj.60.p1.
    assert out["n_mentions"] == 2
    assert len(out["mentions_by_work"]["zztj"]) == 1
    assert len(out["mentions_by_work"]["sanguozhi"]) == 1
    # zztj events come first in groupings (matches timeline ordering).
    first_work = next(w for w in out["mentions_by_work"] if out["mentions_by_work"][w])
    assert first_work == "zztj"


def test_build_one_person_emits_bio_chapters_with_data_url(tmp_path):
    repo = _seed_repo(tmp_path)
    chapters = load_all_chapters(repo)
    person = {"id": "caocao", "primary_name": "曹操",
              "bio_chapters": ["wei.1"]}
    out = build_one_person(person, chapters)
    assert len(out["bio_chapters"]) == 1
    bio = out["bio_chapters"][0]
    assert bio["chapter_id"] == "wei.1"
    assert bio["data_url"] == "data/sanguozhi/wei/01.json"
    assert bio["title"] == "武帝紀"


# ---------- build_index_dict ----------

def test_build_index_dict_emits_flat_name_index_longest_first():
    persons = [
        {"id": "z", "primary_name": "諸葛亮", "aliases": ["諸葛孔明", "孔明"],
         "n_mentions": 1, "bio_chapters": [], "brief": "", "birth_ad": None, "death_ad": None},
        {"id": "c", "primary_name": "曹操", "aliases": [],
         "n_mentions": 2, "bio_chapters": [], "brief": "", "birth_ad": None, "death_ad": None},
    ]
    idx = build_index_dict(persons)
    names = [n for n, _ in idx["name_index"]]
    # Longest-first ordering so "諸葛孔明" matches before "諸葛亮" or "孔明".
    assert names.index("諸葛孔明") < names.index("諸葛亮")
    assert names.index("諸葛亮") < names.index("孔明")
    # Every name → correct person id.
    by_name = dict(idx["name_index"])
    assert by_name["諸葛亮"] == "z"
    assert by_name["孔明"] == "z"
    assert by_name["曹操"] == "c"


def test_build_index_dict_sorts_by_mention_count_desc(tmp_path):
    persons = [
        {"id": "a", "primary_name": "A", "n_mentions": 5,
         "bio_chapters": [], "brief": "", "birth_ad": None, "death_ad": None},
        {"id": "b", "primary_name": "B", "n_mentions": 100,
         "bio_chapters": [], "brief": "", "birth_ad": None, "death_ad": None},
        {"id": "c", "primary_name": "C", "n_mentions": 50,
         "bio_chapters": [], "brief": "", "birth_ad": None, "death_ad": None},
    ]
    idx = build_index_dict(persons)
    assert [r["id"] for r in idx["people"]] == ["b", "c", "a"]


# ---------- write_all (full pipeline) ----------

def test_write_all_emits_index_and_per_person_files(tmp_path):
    repo = _seed_repo(tmp_path)
    cfg = _config_path(tmp_path)
    write_all(repo_root=repo, config_path=cfg)
    out = repo / "site" / "data"
    assert (out / "people.json").exists()
    assert (out / "people" / "caocao.json").exists()
    assert (out / "people" / "dongzhuo.json").exists()

    idx = json.loads((out / "people.json").read_text(encoding="utf-8"))
    ids = [p["id"] for p in idx["people"]]
    assert set(ids) == {"caocao", "dongzhuo"}

    cao = json.loads((out / "people" / "caocao.json").read_text(encoding="utf-8"))
    assert cao["primary_name"] == "曹操"
    # Bio chapter wei.1 not double-counted in mentions.
    bio_ids = {b["chapter_id"] for b in cao["bio_chapters"]}
    assert "wei.1" in bio_ids
    for w, items in cao["mentions_by_work"].items():
        for m in items:
            assert m["chapter_id"] != "wei.1"
