"""Tests for tools/batch_fetch_hhs.py — config + parts handling + multi-page concat."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.batch_fetch_hhs import (
    CONFIG_PATH_DEFAULT,
    FetchError,
    FetchResult,
    derive_paths,
    load_config,
    process_one,
    run,
)
from tools.check import validate_text_file

FIXTURE = Path(__file__).parent / "fixtures" / "wikisource_hhs_sample.html"


# ---------- load_config ----------

def test_real_hhs_config_loads():
    cfg = load_config(CONFIG_PATH_DEFAULT)
    juans = [int(e["juan"]) for e in cfg]
    # Must include the agreed-upon range: benji 8/9/10, liezhuan 57–80.
    assert 8 in juans and 9 in juans and 10 in juans
    assert 57 in juans and 80 in juans
    # Categories all in the allowed set.
    for e in cfg:
        assert e["category"] in {"benji", "liezhuan", "zhi"}


def test_load_config_rejects_missing_juan(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- { category: benji }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'juan'"):
        load_config(bad)


def test_load_config_rejects_unknown_category(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- { juan: 8, category: poetry }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        load_config(bad)


# ---------- derive_paths ----------

def test_derive_paths_no_parts(tmp_path):
    text, urls = derive_paths({"juan": 8, "category": "benji"}, repo_root=tmp_path)
    assert text == tmp_path / "texts" / "houhanshu" / "08.md"
    assert len(urls) == 1
    url, src = urls[0]
    assert url == "https://zh.wikisource.org/wiki/後漢書/卷8"
    assert src == tmp_path / "sources" / "wikisource" / "houhanshu" / "08.html"


def test_derive_paths_with_parts(tmp_path):
    entry = {"juan": 74, "category": "liezhuan", "parts": ["上", "下"]}
    text, urls = derive_paths(entry, repo_root=tmp_path)
    assert text == tmp_path / "texts" / "houhanshu" / "74.md"
    assert [u for u, _ in urls] == [
        "https://zh.wikisource.org/wiki/後漢書/卷74上",
        "https://zh.wikisource.org/wiki/後漢書/卷74下",
    ]
    assert urls[0][1].name == "74-上.html"
    assert urls[1][1].name == "74-下.html"


def test_derive_paths_only_xia(tmp_path):
    entry = {"juan": 10, "category": "benji", "parts": ["下"]}
    _text, urls = derive_paths(entry, repo_root=tmp_path)
    assert len(urls) == 1
    assert "卷10下" in urls[0][0]
    assert urls[0][1].name == "10-下.html"


# ---------- process_one ----------

def _fake_fetcher(html_bytes: bytes):
    calls = []
    def fetcher(url):
        calls.append(url)
        return html_bytes
    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def test_process_one_single_page(tmp_path):
    fetcher = _fake_fetcher(FIXTURE.read_bytes())
    entry = {"juan": 8, "category": "benji", "title": "孝靈帝紀"}
    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-02",
                         fetcher=fetcher, mode="auto")
    assert isinstance(result, FetchResult)
    assert result.text_path == tmp_path / "texts" / "houhanshu" / "08.md"
    assert result.text_path.exists()
    assert validate_text_file(result.text_path) == [], "rendered file must validate"
    # title in frontmatter is the override since we passed `title` in config.
    content = result.text_path.read_text(encoding="utf-8")
    assert "title: 孝靈帝紀" in content


def test_process_one_concatenates_parts(tmp_path):
    """Two parts: each contributes its own paragraphs; document order preserved, IDs renumbered."""
    fetcher = _fake_fetcher(FIXTURE.read_bytes())
    entry = {"juan": 60, "category": "liezhuan", "parts": ["上", "下"]}
    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-02",
                         fetcher=fetcher, mode="auto")
    # Each fetch returns the fixture (2 substantive paragraphs). With 2 parts → 4 segments.
    assert result.n_segments == 4
    # Both source HTMLs were saved, separately.
    assert len(result.source_paths) == 2
    assert all(p.exists() for p in result.source_paths)
    # Frontmatter records `parts` provenance.
    content = result.text_path.read_text(encoding="utf-8")
    assert "parts:" in content


def test_process_one_uses_no_fetch_cache(tmp_path):
    src = tmp_path / "sources" / "wikisource" / "houhanshu" / "08.html"
    src.parent.mkdir(parents=True)
    src.write_bytes(FIXTURE.read_bytes())

    def angry(_url):
        raise AssertionError("must not hit network in --no-fetch mode")

    entry = {"juan": 8, "category": "benji", "title": "孝靈帝紀"}
    result = process_one(entry, repo_root=tmp_path, retrieved="2026-05-02",
                         fetcher=angry, mode="no-fetch")
    assert isinstance(result, FetchResult)


def test_process_one_no_fetch_errors_when_cache_missing(tmp_path):
    entry = {"juan": 8, "category": "benji", "title": "孝靈帝紀"}
    with pytest.raises(FileNotFoundError, match="does not exist"):
        process_one(entry, repo_root=tmp_path, retrieved="2026-05-02",
                    fetcher=lambda _u: b"", mode="no-fetch")


# ---------- run() driver ----------

def test_run_only_filter(tmp_path):
    cfg = [
        {"juan": 8,  "category": "benji",    "title": "孝靈帝紀"},
        {"juan": 57, "category": "liezhuan"},
    ]
    fetcher = _fake_fetcher(FIXTURE.read_bytes())
    results = list(run(cfg, repo_root=tmp_path, only={8}, fetcher=fetcher,
                       sleeper=lambda _: None, retrieved="2026-05-02"))
    assert len(results) == 1
    assert isinstance(results[0], FetchResult)
    assert fetcher.calls == ["https://zh.wikisource.org/wiki/後漢書/卷8"]


def test_run_continues_after_error(tmp_path):
    cfg = [
        {"juan": 8, "category": "benji", "title": "孝靈帝紀"},
        {"juan": 9, "category": "benji", "title": "孝獻帝紀"},
    ]

    def flaky(url):
        if url.endswith("/卷8"):
            raise OSError("simulated network blip")
        return FIXTURE.read_bytes()

    results = list(run(cfg, repo_root=tmp_path, fetcher=flaky,
                       sleeper=lambda _: None, retrieved="2026-05-02"))
    assert isinstance(results[0], FetchError)
    assert isinstance(results[1], FetchResult)
