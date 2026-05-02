"""Tests for tools/check.py — repo-wide validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.check import run, validate_text_file
from tools.segment import file_segments_sha256, parse_text


def _make_valid_md() -> str:
    """Build a valid texts/ Markdown string and stamp segments_sha256 into it."""
    body = (
        '<a id="wei.1.p1"></a>\n'
        "甲乙丙丁。\n\n"
        '<a id="wei.1.p2"></a>\n'
        "戊己庚辛。\n"
    )
    fm = (
        "---\n"
        "work: sanguozhi\n"
        "work_title: 三國志\n"
        "book: wei\n"
        "book_title: 魏書\n"
        "juan: 1\n"
        "title: 武帝紀\n"
        "author: 陳壽\n"
        "script: traditional\n"
        "source:\n"
        "  id: ctext\n"
        "  url: https://ctext.org/sanguozhi/1\n"
        "  retrieved: '2026-05-01'\n"
        f"  sha256: {'a' * 64}\n"
        "segments_sha256: PLACEHOLDER\n"
        "---\n\n"
    )
    intermediate = fm + body
    parsed = parse_text(intermediate)
    correct = file_segments_sha256(parsed.segments)
    return intermediate.replace("PLACEHOLDER", correct)


def _write(tmp_path: Path, content: str, name: str = "01-x.md") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------- validate_text_file ----------

def test_valid_file_has_no_errors(tmp_path):
    f = _write(tmp_path, _make_valid_md())
    assert validate_text_file(f) == []


def test_missing_required_frontmatter_field_is_reported(tmp_path):
    md = _make_valid_md().replace("author: 陳壽\n", "")
    f = _write(tmp_path, md)
    errs = validate_text_file(f)
    assert any("'author'" in e for e in errs)


def test_segments_sha256_mismatch_is_reported(tmp_path):
    md = _make_valid_md()
    real_hash = md.split("segments_sha256: ")[1].split("\n")[0]
    # Use 'b' * 64 (not '0' * 64) — PyYAML parses all-zero strings as int 0.
    md_bad = md.replace(real_hash, "b" * 64)
    f = _write(tmp_path, md_bad)
    errs = validate_text_file(f)
    assert any("segments_sha256 mismatch" in e for e in errs)


def test_segments_sha256_int_value_is_reported(tmp_path):
    """PyYAML parses '0000...' as int 0; check.py must reject non-string sha256."""
    md = _make_valid_md()
    real_hash = md.split("segments_sha256: ")[1].split("\n")[0]
    md_bad = md.replace(real_hash, "0" * 64)
    f = _write(tmp_path, md_bad)
    errs = validate_text_file(f)
    assert any("64-char lowercase hex string" in e for e in errs)


def test_bad_script_value_is_reported(tmp_path):
    md = _make_valid_md().replace("script: traditional", "script: cuneiform")
    f = _write(tmp_path, md)
    errs = validate_text_file(f)
    assert any("script" in e for e in errs)


def test_unknown_source_id_is_reported(tmp_path):
    md = _make_valid_md().replace("id: ctext", "id: bogus_source")
    f = _write(tmp_path, md)
    errs = validate_text_file(f)
    assert any("source.id" in e and "bogus_source" in e for e in errs)


def test_invalid_sha256_format_is_reported(tmp_path):
    md = _make_valid_md().replace(f"sha256: {'a' * 64}", "sha256: NOTAHASH")
    f = _write(tmp_path, md)
    errs = validate_text_file(f)
    assert any("sha256" in e and "hex" in e for e in errs)


def test_segment_id_not_matching_book_juan_is_reported(tmp_path):
    md = _make_valid_md().replace('<a id="wei.1.p1"></a>', '<a id="shu.5.p1"></a>')
    # Recompute segments_sha256 since the segment text didn't change but ID did,
    # and segments_sha256 is order-by-ID; we want the test to focus on the id-format error.
    parsed = parse_text(md)
    correct = file_segments_sha256(parsed.segments)
    md = md.replace(
        md.split("segments_sha256: ")[1].split("\n")[0],
        correct,
    )
    f = _write(tmp_path, md)
    errs = validate_text_file(f)
    assert any("segment id" in e for e in errs)


def test_parse_error_is_surfaced(tmp_path):
    f = _write(tmp_path, "no frontmatter here\n")
    errs = validate_text_file(f)
    assert any("parse error" in e for e in errs)


# ---------- run() over a directory ----------

def test_run_reports_all_files_and_failure_count(tmp_path, capsys):
    good_dir = tmp_path / "good_repo"
    good_dir.mkdir()
    _write(good_dir, _make_valid_md(), "01-good.md")
    bad = _make_valid_md().replace("script: traditional", "script: bad")
    _write(good_dir, bad, "02-bad.md")

    n_files, n_failed = run([good_dir])
    captured = capsys.readouterr().out
    assert n_files == 2
    assert n_failed == 1
    assert "OK" in captured and "FAIL" in captured


def test_run_warns_on_nonexistent_path(tmp_path, capsys):
    n_files, n_failed = run([tmp_path / "does_not_exist"])
    err = capsys.readouterr().err
    assert n_files == 0 and n_failed == 0
    assert "does not exist" in err


def test_run_accepts_single_file(tmp_path):
    f = _write(tmp_path, _make_valid_md())
    n_files, n_failed = run([f])
    assert n_files == 1 and n_failed == 0


def test_existing_repo_sample_passes():
    """The sample texts/ file in the repo must validate (regression guard)."""
    repo_root = Path(__file__).resolve().parents[1]
    sample = repo_root / "texts" / "sanguozhi" / "wei" / "01.md"
    if not sample.exists():
        pytest.skip("sample not present")
    assert validate_text_file(sample) == []


# ---------- annotation validation ----------

import yaml  # noqa: E402

from tools.check import validate_annotation_file  # noqa: E402


def _make_repo_with_text_and_anno(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tmp repo containing one valid text + one valid annotations YAML.
    Returns (repo_root, annotations_path)."""
    text_dir = tmp_path / "texts" / "sanguozhi" / "wei"
    text_dir.mkdir(parents=True)
    text = _make_valid_md()
    (text_dir / "01.md").write_text(text, encoding="utf-8")

    ann_dir = tmp_path / "annotations" / "sanguozhi" / "wei"
    ann_dir.mkdir(parents=True)
    ann_path = ann_dir / "01.yaml"
    ann_doc = {
        "chapter": "wei.1",
        "source": {
            "id": "wikisource",
            "url": "https://zh.wikisource.org/wiki/三國志/卷01",
            "retrieved": "2026-05-01",
            "sha256": "a" * 64,
        },
        "annotations": [
            {"id": "wei.1.p1.a1", "anchor": "wei.1.p1", "at": 2, "length": 0,
             "type": "pei", "text": "《魏書》曰：注一"},
        ],
    }
    ann_path.write_text(yaml.safe_dump(ann_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_path, ann_path


def test_valid_annotation_file_has_no_errors(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    assert validate_annotation_file(ann_path, repo_root=repo) == []


def test_annotation_unknown_type_is_reported(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"][0]["type"] = "bogus"
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = validate_annotation_file(ann_path, repo_root=repo)
    assert any("type=" in e and "bogus" in e for e in errs)


def test_annotation_anchor_must_exist_in_texts(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"][0]["anchor"] = "wei.1.p99"  # nonexistent
    doc["annotations"][0]["id"] = "wei.1.p99.a1"   # keep id pattern legal
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = validate_annotation_file(ann_path, repo_root=repo)
    assert any("anchor 'wei.1.p99'" in e for e in errs)


def test_annotation_at_out_of_range_is_reported(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"][0]["at"] = 9999  # text is only 4 chars
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = validate_annotation_file(ann_path, repo_root=repo)
    assert any("out of range" in e for e in errs)


def test_annotation_id_must_match_pattern(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"][0]["id"] = "not-an-id"
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = validate_annotation_file(ann_path, repo_root=repo)
    assert any("does not match" in e for e in errs)


def test_duplicate_annotation_ids_reported(tmp_path):
    repo, ann_path = _make_repo_with_text_and_anno(tmp_path)
    doc = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
    doc["annotations"].append(dict(doc["annotations"][0]))  # exact duplicate
    ann_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = validate_annotation_file(ann_path, repo_root=repo)
    assert any("duplicate annotation id" in e for e in errs)


def test_run_validates_both_texts_and_annotations(tmp_path, capsys):
    repo, _ann_path = _make_repo_with_text_and_anno(tmp_path)
    n, failed = run([repo / "texts", repo / "annotations"], repo_root=repo)
    assert n == 2 and failed == 0
    out = capsys.readouterr().out
    assert "01.md" in out
    assert "01.yaml" in out


def test_existing_repo_annotation_sample_passes():
    """The repo's actual annotations/wei/01.yaml must validate (regression guard)."""
    repo_root = Path(__file__).resolve().parents[1]
    sample = repo_root / "annotations" / "sanguozhi" / "wei" / "01.yaml"
    if not sample.exists():
        pytest.skip("annotation not present yet")
    assert validate_annotation_file(sample, repo_root=repo_root) == []
