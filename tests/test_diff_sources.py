"""Tests for tools/diff_sources.py — focus on round-trip and classification."""
from __future__ import annotations

import pytest

from tools.diff_sources import apply_ops, classify, compute_ops


# ---------- compute_ops + apply_ops round-trip ----------

ROUND_TRIP_CASES = [
    ("identical", "太祖武皇帝", "太祖武皇帝"),
    ("single_replace", "太祖武皇帝", "太祖文皇帝"),
    ("trad_to_simp", "太祖武皇帝，沛國譙人也", "太祖武皇帝，沛国谯人也"),
    ("insert_only", "太祖武皇帝", "太祖魏武皇帝"),
    ("delete_only", "太祖武皇帝也", "太祖武皇帝"),
    ("insert_at_end", "太祖武皇帝", "太祖武皇帝也"),
    ("insert_at_start", "太祖武皇帝", "魏太祖武皇帝"),
    ("complete_replace", "甲乙丙", "丁戊己"),
    ("multi_diff", "ABCDEFGHIJ", "AXCDFGHIJZ"),
    ("empty_to_text", "", "新增"),
    ("text_to_empty", "刪除", ""),
    ("punct_only", "太祖。武帝，", "太祖，武帝。"),
]


@pytest.mark.parametrize("name,canonical,source", ROUND_TRIP_CASES, ids=[c[0] for c in ROUND_TRIP_CASES])
def test_compute_then_apply_reconstructs_source(name, canonical, source):
    ops = compute_ops(canonical, source)
    assert apply_ops(canonical, ops) == source


def test_identical_inputs_produce_no_ops():
    assert compute_ops("太祖武皇帝", "太祖武皇帝") == []


def test_replace_op_shape():
    ops = compute_ops("國", "国")
    assert ops == [{"op": "replace", "at": 0, "length": 1, "from": "國", "to": "国"}]


def test_insert_op_shape():
    ops = compute_ops("AB", "AXB")
    assert ops == [{"op": "insert", "at": 1, "text": "X"}]


def test_delete_op_shape():
    ops = compute_ops("ABC", "AC")
    assert ops == [{"op": "delete", "at": 1, "length": 1, "text": "B"}]


# ---------- classify ----------

def test_classify_identical():
    r = classify("太祖武皇帝", "太祖武皇帝")
    assert r == {"kind": "equal", "equal_normalized": True, "ops": []}


def test_classify_variant_char_for_simp_trad():
    r = classify("太祖武皇帝，沛國譙人也", "太祖武皇帝，沛国谯人也")
    assert r["kind"] == "variant_char"
    assert r["equal_normalized"] is True
    assert any(o["op"] == "replace" for o in r["ops"])


def test_classify_variant_char_for_punctuation_only():
    r = classify("太祖，武帝。", "太祖。武帝，")
    assert r["kind"] == "variant_char"
    assert r["equal_normalized"] is True


def test_classify_textual_for_real_addition():
    r = classify("太祖武皇帝沛國譙人也", "太祖武皇帝沛國譙縣人也")
    assert r["kind"] == "textual"
    assert r["equal_normalized"] is False


# ---------- apply_ops validation ----------

def test_apply_ops_rejects_overlapping_ops():
    canonical = "ABCDE"
    bad = [
        {"op": "replace", "at": 1, "length": 2, "from": "BC", "to": "X"},
        {"op": "replace", "at": 2, "length": 1, "from": "C", "to": "Y"},
    ]
    with pytest.raises(ValueError, match="overlap|order"):
        apply_ops(canonical, bad)


def test_apply_ops_rejects_replace_with_wrong_from():
    with pytest.raises(ValueError, match="'from' does not match"):
        apply_ops("ABC", [{"op": "replace", "at": 0, "length": 1, "from": "Z", "to": "Y"}])


def test_apply_ops_rejects_op_past_end():
    with pytest.raises(ValueError, match="past canonical length"):
        apply_ops("ABC", [{"op": "delete", "at": 5, "length": 1, "text": "X"}])
