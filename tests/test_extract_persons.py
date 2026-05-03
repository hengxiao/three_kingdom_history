"""Unit tests for tools/extract_persons.py — segment-local matching algorithm
(no filesystem walk; we test the core span-finder directly)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.extract_persons import (
    build_person_annotations,
    find_person_spans_in_segment,
    load_people_config,
    merge_persons_into_file,
)


# ---- find_person_spans_in_segment ----

CAOCAO = {
    "id": "caocao",
    "primary_name": "曹操",
    "given_name": "操",
    "given_name_blockers": ["操行", "節操", "操守"],
    "aliases": [],
}

LIUBEI = {
    "id": "liubei",
    "primary_name": "劉備",
    "given_name": "備",
    "given_name_blockers": ["完備", "戒備", "防備"],
    "aliases": ["劉玄德"],
}

GUANYU = {
    "id": "guanyu",
    "primary_name": "關羽",
    "given_name": "羽",
    "given_name_blockers": ["羽毛", "羽翼"],
    "aliases": ["關雲長"],
}


def test_pass1_finds_primary_name():
    spans = find_person_spans_in_segment("曹操使司馬荀彧。", [CAOCAO])
    assert len(spans) == 1
    assert spans[0]["text"] == "曹操"
    assert spans[0]["via"] == "primary"
    assert spans[0]["at"] == 0
    assert spans[0]["length"] == 2


def test_pass1_finds_alias():
    spans = find_person_spans_in_segment("劉玄德與關羽戰。", [LIUBEI, GUANYU])
    by_text = {sp["text"]: sp for sp in spans}
    assert "劉玄德" in by_text
    assert by_text["劉玄德"]["via"] == "alias"
    assert "關羽" in by_text
    assert by_text["關羽"]["via"] == "primary"


def test_pass2_given_name_after_primary():
    """The bug from zztj.61.p10: 操 after 曹操 in same segment."""
    text = "曹操使司馬荀彧守鄄城，操乃引軍還。"
    spans = find_person_spans_in_segment(text, [CAOCAO])
    surfaces = [(sp["at"], sp["text"], sp["via"]) for sp in spans]
    # First span: 曹操 (primary)
    assert surfaces[0][1] == "曹操"
    assert surfaces[0][2] == "primary"
    # Second span: 操 (given_name) at the position right after 「，」
    given_spans = [sp for sp in spans if sp["via"] == "given_name"]
    assert len(given_spans) == 1
    assert given_spans[0]["text"] == "操"
    assert text[given_spans[0]["at"]] == "操"


def test_pass2_blocked_by_compound():
    """『操行』should NOT count as 曹操 even after 曹操 has appeared."""
    text = "曹操有大功，操行端正。"
    spans = find_person_spans_in_segment(text, [CAOCAO])
    given_spans = [sp for sp in spans if sp["via"] == "given_name"]
    # 操 in 操行 is blocked
    assert len(given_spans) == 0


def test_pass2_skipped_when_no_primary():
    """Bare 操 in a segment without 曹操 must NOT match."""
    text = "操行端正，諸事齊備。"
    spans = find_person_spans_in_segment(text, [CAOCAO, LIUBEI])
    assert spans == []


def test_pass2_matches_before_primary_too():
    """given_name BEFORE primary in the same segment SHOULD match — classical
    narrative often opens a paragraph with the single-char name (「卓還長安」)
    and re-establishes the full name later. As long as primary appears somewhere
    in the segment, all given_name occurrences are linked to that person."""
    text = "操有大計，曹操遂行之。"
    spans = find_person_spans_in_segment(text, [CAOCAO])
    by_via = [(sp["text"], sp["via"]) for sp in spans]
    assert ("操", "given_name") in by_via
    assert ("曹操", "primary") in by_via


def test_no_overlap_between_pass1_and_pass2():
    """The 操 inside 曹操 must not be re-counted as a given_name match."""
    text = "曹操又使曹操行之。"
    spans = find_person_spans_in_segment(text, [CAOCAO])
    # Two 曹操 primary matches; 操 inside each is part of 曹操 already.
    primaries = [sp for sp in spans if sp["via"] == "primary"]
    given = [sp for sp in spans if sp["via"] == "given_name"]
    assert len(primaries) == 2
    # 「曹操行」: the 操 here is inside 曹操 so it's covered by primary; 行 makes 操行 — but
    # since 操 is covered, blocker check doesn't even run. No given_name span emitted.
    assert len(given) == 0


def test_alias_doesnt_overlap_primary():
    """劉玄德 (alias) and 劉備 (primary) shouldn't both fire on the same range."""
    text = "劉備又稱劉玄德。"
    spans = find_person_spans_in_segment(text, [LIUBEI])
    by_via = [(sp["text"], sp["via"]) for sp in spans]
    assert ("劉備", "primary") in by_via
    assert ("劉玄德", "alias") in by_via


def test_pass2_skips_inside_other_persons_pass1_span():
    """關雲長 (alias of guanyu) contains 雲. zhaoyun's given_name 雲 inside that
    range must not be claimed by zhaoyun."""
    zhaoyun = {"id": "zhaoyun", "primary_name": "趙雲", "given_name": "雲",
               "given_name_blockers": [], "aliases": []}
    text = "趙雲與關雲長並戰，雲奮力斬之。"
    spans = find_person_spans_in_segment(text, [GUANYU, zhaoyun])
    # 雲 inside 關雲長 must not be a given_name match for zhaoyun.
    given = [sp for sp in spans if sp["via"] == "given_name"]
    assert len(given) == 1
    assert text[given[0]["at"]] == "雲"
    # The matched 雲 should be at the position after 「，」, not inside 關雲長.
    assert given[0]["at"] > text.index("關雲長") + len("關雲長") - 1


def test_blocker_works_on_either_side():
    """完備 should block 備 even though 備 is the SECOND char of the bigram."""
    text = "劉備守鄄城，城防完備。"
    spans = find_person_spans_in_segment(text, [LIUBEI])
    given = [sp for sp in spans if sp["via"] == "given_name"]
    assert given == []


# ---- chapter_aliases (廟號/諡號 scoped to a book) ----

CAOCAO_WITH_CH_ALIAS = {
    **CAOCAO,
    "chapter_aliases": {"wei": ["太祖", "武皇帝", "武帝"]},
}


def test_chapter_alias_matches_in_scoped_book():
    """太祖 in 魏書 chapters resolves to 曹操 (chapter_alias)."""
    text = "太祖武皇帝，姓曹，諱操，字孟德。"
    spans = find_person_spans_in_segment(text, [CAOCAO_WITH_CH_ALIAS], book="wei")
    by_via = {sp["text"]: sp["via"] for sp in spans}
    assert by_via.get("太祖") == "chapter_alias"
    assert by_via.get("武皇帝") == "chapter_alias"
    # 操 (given_name) should also fire since primary's chapter_alias appeared.
    given = [sp for sp in spans if sp["via"] == "given_name"]
    assert any(sp["text"] == "操" for sp in given)


def test_chapter_alias_does_NOT_match_in_other_book():
    """太祖 in 吳書 chapters must NOT silently resolve to 曹操."""
    text = "太祖開吳越基業。"
    spans = find_person_spans_in_segment(text, [CAOCAO_WITH_CH_ALIAS], book="wu")
    # No match — caocao's 太祖 is wei-only.
    assert all(sp["person_id"] != "caocao" for sp in spans)


def test_chapter_alias_no_book_passed_means_no_match():
    """When `book` is None (e.g., zztj fallback), chapter_aliases stay disabled."""
    text = "太祖武皇帝。"
    spans = find_person_spans_in_segment(text, [CAOCAO_WITH_CH_ALIAS], book=None)
    assert all(sp["person_id"] != "caocao" for sp in spans)


# ---- cross-segment given_name carry ----

def _seed_chapter_with_carry(tmp_path: Path, *, gap_size: int) -> Path:
    """p1 establishes 董卓; then `gap_size` paragraphs with no reference; final
    paragraph references 卓 alone. Used to verify the carry window."""
    paras = ['<a id="zztj.60.p1"></a>\n董卓進京，廢帝立獻帝。']
    for i in range(2, 2 + gap_size):
        paras.append(f'<a id="zztj.60.p{i}"></a>\n是歲，無關事。')
    last = 2 + gap_size
    paras.append(f'<a id="zztj.60.p{last}"></a>\n卓既誅，李傕等亂政。')
    body = (
        "---\n"
        "work: zztj\nwork_title: 資治通鑑\nbook: zztj\nbook_title: 資治通鑑\n"
        "juan: 60\ntitle: 漢紀五十二\nauthor: 司馬光\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        + "\n\n".join(paras) + "\n"
    )
    body = _stamp_segments_sha256(body)
    p = tmp_path / "060.md"
    p.write_text(body, encoding="utf-8")
    return p, last


def test_cross_segment_carry_within_window():
    import tempfile
    dongzhuo = {"id": "dongzhuo", "primary_name": "董卓",
                "given_name": "卓", "given_name_blockers": [], "aliases": []}
    # gap of 7 → final at p9 = 8 segments from p1 → at the window boundary, should match
    with tempfile.TemporaryDirectory() as td:
        text_path, last = _seed_chapter_with_carry(Path(td), gap_size=7)
        anns = build_person_annotations(text_path, [dongzhuo])
        last_seg = f"zztj.60.p{last}"
        last_anns = [a for a in anns if a["anchor"] == last_seg]
        assert any(a["text"] == "卓" and a["via"] == "given_name_carry"
                   for a in last_anns), f"expected 卓 carry on {last_seg}"


def test_cross_segment_carry_past_window():
    import tempfile
    dongzhuo = {"id": "dongzhuo", "primary_name": "董卓",
                "given_name": "卓", "given_name_blockers": [], "aliases": []}
    # gap of 9 with no intermediate carry → final at p11 = 10 segs from p1, past window
    with tempfile.TemporaryDirectory() as td:
        text_path, last = _seed_chapter_with_carry(Path(td), gap_size=9)
        anns = build_person_annotations(text_path, [dongzhuo])
        last_seg = f"zztj.60.p{last}"
        last_anns = [a for a in anns if a["anchor"] == last_seg]
        assert all(a["text"] != "卓" for a in last_anns), \
            f"expected NO 卓 carry on {last_seg} (past window)"


def test_chapter_alias_two_persons_distinguished_by_book():
    """太祖 in 魏書 → 曹操; 太祖 in 吳書 → 孫權. Same surface, different book."""
    sunquan_with = {"id": "sunquan", "primary_name": "孫權", "given_name": "權",
                    "given_name_blockers": [], "aliases": [],
                    "chapter_aliases": {"wu": ["太祖", "大帝"]}}
    text = "太祖即位，改元黃龍。"
    wei_spans = find_person_spans_in_segment(text, [CAOCAO_WITH_CH_ALIAS, sunquan_with], book="wei")
    wu_spans = find_person_spans_in_segment(text, [CAOCAO_WITH_CH_ALIAS, sunquan_with], book="wu")
    assert any(sp["person_id"] == "caocao" for sp in wei_spans)
    assert all(sp["person_id"] != "sunquan" for sp in wei_spans)
    assert any(sp["person_id"] == "sunquan" for sp in wu_spans)
    assert all(sp["person_id"] != "caocao" for sp in wu_spans)


# ---- build_person_annotations (over a parsed file) ----

def _stamp_segments_sha256(text: str) -> str:
    from tools.segment import file_segments_sha256, parse_text
    parsed = parse_text(text)
    return text.replace("PLACEHOLDER", file_segments_sha256(parsed.segments))


def _seed_chapter(tmp_path: Path) -> Path:
    body = (
        "---\n"
        "work: zztj\nwork_title: 資治通鑑\nbook: zztj\nbook_title: 資治通鑑\n"
        "juan: 61\ntitle: 漢紀五十三\nauthor: 司馬光\nscript: traditional\n"
        "source:\n  id: wikisource\n  url: x\n  retrieved: '2026-05-03'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
        '<a id="zztj.61.p10"></a>\n'
        '曹操使司馬荀彧守鄄城，操乃引軍還。\n\n'
        '<a id="zztj.61.p20"></a>\n'
        '此段無人名，操行純正。\n'
    )
    body = _stamp_segments_sha256(body)
    p = tmp_path / "061.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_build_person_annotations_emits_ids_per_segment(tmp_path):
    text_path = _seed_chapter(tmp_path)
    anns = build_person_annotations(text_path, [CAOCAO])
    # p10 has 曹操 + 操; p20 has only 操行 (blocked).
    p10 = [a for a in anns if a["anchor"] == "zztj.61.p10"]
    p20 = [a for a in anns if a["anchor"] == "zztj.61.p20"]
    assert len(p10) == 2
    assert {a["text"] for a in p10} == {"曹操", "操"}
    # IDs numbered per segment
    assert {a["id"] for a in p10} == {"zztj.61.p10.h1", "zztj.61.p10.h2"}
    # All carry person_id
    for a in p10:
        assert a["person_id"] == "caocao"
        assert a["type"] == "person"
    # p20 produced no annotations
    assert p20 == []


# ---- merge_persons_into_file ----

def test_merge_replaces_existing_person_entries(tmp_path):
    yml = tmp_path / "061.yaml"
    yml.write_text(yaml.safe_dump({
        "chapter": "zztj.61",
        "annotations": [
            {"id": "zztj.61.p1.t1", "anchor": "zztj.61.p1", "at": 0, "length": 2,
             "type": "temporal", "text": "正月", "year_ad": 194, "kind": "absolute"},
            # Stale person annotation that must be overwritten.
            {"id": "zztj.61.p1.h1", "anchor": "zztj.61.p1", "at": 5, "length": 2,
             "type": "person", "person_id": "stale", "text": "不存", "via": "primary"},
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    new_persons = [
        {"id": "zztj.61.p10.h1", "anchor": "zztj.61.p10", "at": 0, "length": 2,
         "type": "person", "person_id": "caocao", "text": "曹操", "via": "primary"},
    ]
    n = merge_persons_into_file(yml, new_persons)
    assert n == 1
    doc = yaml.safe_load(yml.read_text(encoding="utf-8"))
    persons = [a for a in doc["annotations"] if a["type"] == "person"]
    assert len(persons) == 1
    assert persons[0]["person_id"] == "caocao"
    # Temporal preserved
    temporals = [a for a in doc["annotations"] if a["type"] == "temporal"]
    assert len(temporals) == 1


# ---- load_people_config ----

def test_load_config_rejects_duplicate_ids(tmp_path):
    bad = tmp_path / "people.yaml"
    bad.write_text(
        "- { id: x, primary_name: A }\n- { id: x, primary_name: B }\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_people_config(bad)
