"""Tests for tools/batch_fetch.py — config loading, path derivation, batch driver.

The driver is tested with an injected fetcher (no real network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.batch_fetch import (
    BOOK_TITLES,
    CONFIG_PATH_DEFAULT,
    FetchError,
    FetchResult,
    batch,
    derive_paths,
    load_config,
)
from tools.check import validate_text_file

FIXTURE = Path(__file__).parent / "fixtures" / "wikisource_sample.html"


# ---------- load_config ----------

def test_real_config_loads_and_covers_65_juans():
    cfg = load_config(CONFIG_PATH_DEFAULT)
    assert len(cfg) == 65
    juans = [e["ctext_juan"] for e in cfg]
    assert juans == list(range(1, 66))
    books = {e["book"] for e in cfg}
    assert books == set(BOOK_TITLES)
    # Wei has 30, Shu has 15, Wu has 20.
    counts = {b: sum(1 for e in cfg if e["book"] == b) for b in BOOK_TITLES}
    assert counts == {"wei": 30, "shu": 15, "wu": 20}
    # Local juan is monotonic within each book starting at 1.
    for book in BOOK_TITLES:
        local = [e["juan"] for e in cfg if e["book"] == book]
        assert local == list(range(1, len(local) + 1))


def test_load_config_rejects_missing_keys(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- ctext_juan: 1\n  book: wei\n", encoding="utf-8")  # missing 'juan'
    with pytest.raises(ValueError, match="missing required key"):
        load_config(bad)


def test_load_config_rejects_unknown_book(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- { ctext_juan: 1, book: jin, juan: 1 }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown book"):
        load_config(bad)


# ---------- derive_paths ----------

def test_derive_paths_uses_two_digit_local_juan(tmp_path):
    text, src = derive_paths({"book": "wei", "juan": 7}, repo_root=tmp_path)
    assert text == tmp_path / "texts" / "sanguozhi" / "wei" / "07.md"
    assert src == tmp_path / "sources" / "wikisource" / "sanguozhi" / "wei" / "07.html"


def test_derive_paths_handles_juan_above_9():
    text, _ = derive_paths({"book": "wu", "juan": 20}, repo_root=Path("/r"))
    assert text == Path("/r/texts/sanguozhi/wu/20.md")


# ---------- batch driver (uses fixture HTML, no network) ----------

@pytest.fixture
def fixture_bytes() -> bytes:
    """Wikisource fixture is URL-agnostic — same content works for any 卷NN URL."""
    return FIXTURE.read_bytes()


def _fake_fetcher(html_bytes: bytes):
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return html_bytes

    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def test_batch_processes_each_entry(tmp_path, fixture_bytes):
    config = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 2, "book": "wei", "juan": 2},
    ]
    fetcher = _fake_fetcher(fixture_bytes)
    sleep_calls: list[float] = []

    results = list(batch(
        config,
        fetcher=fetcher,
        repo_root=tmp_path,
        sleep_seconds=0.5,
        sleeper=sleep_calls.append,
        retrieved="2026-05-01",
    ))

    assert len(results) == 2
    assert all(isinstance(r, FetchResult) for r in results)
    # Both files written and validate cleanly against doc/format.md.
    for r in results:
        assert r.text_path.exists()
        assert r.source_path.exists()
        assert validate_text_file(r.text_path) == [], r.text_path
    # Throttle fired exactly once between the two requests.
    assert sleep_calls == [0.5]
    assert fetcher.calls == [
        "https://zh.wikisource.org/wiki/三國志/卷01",
        "https://zh.wikisource.org/wiki/三國志/卷02",
    ]


def test_batch_only_filter(tmp_path, fixture_bytes):
    config = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 31, "book": "shu", "juan": 1},
        {"ctext_juan": 46, "book": "wu", "juan": 1},
    ]
    fetcher = _fake_fetcher(fixture_bytes)
    results = list(batch(
        config,
        fetcher=fetcher,
        repo_root=tmp_path,
        only={31},
        sleeper=lambda _: None,
        retrieved="2026-05-01",
    ))
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, FetchResult)
    assert r.text_path == tmp_path / "texts" / "sanguozhi" / "shu" / "01.md"
    assert fetcher.calls == ["https://zh.wikisource.org/wiki/三國志/卷31"]


def test_batch_resume_skips_existing(tmp_path, fixture_bytes):
    config = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 2, "book": "wei", "juan": 2},
    ]
    # Pre-create the first text file so resume should skip it.
    (tmp_path / "texts" / "sanguozhi" / "wei").mkdir(parents=True)
    (tmp_path / "texts" / "sanguozhi" / "wei" / "01.md").write_text("placeholder", encoding="utf-8")

    fetcher = _fake_fetcher(fixture_bytes)
    results = list(batch(
        config,
        fetcher=fetcher,
        repo_root=tmp_path,
        resume=True,
        sleeper=lambda _: None,
        retrieved="2026-05-01",
    ))
    assert len(results) == 1
    assert fetcher.calls == ["https://zh.wikisource.org/wiki/三國志/卷02"]
    # The placeholder file is left intact.
    assert (tmp_path / "texts/sanguozhi/wei/01.md").read_text() == "placeholder"


def test_batch_no_fetch_uses_existing_source(tmp_path, fixture_bytes):
    config = [{"ctext_juan": 1, "book": "wei", "juan": 1}]
    src = tmp_path / "sources" / "wikisource" / "sanguozhi" / "wei" / "01.html"
    src.parent.mkdir(parents=True)
    src.write_bytes(fixture_bytes)

    def angry_fetcher(_url: str) -> bytes:
        raise AssertionError("network must not be hit when --no-fetch")

    results = list(batch(
        config,
        fetcher=angry_fetcher,
        repo_root=tmp_path,
        no_fetch=True,
        sleeper=lambda _: None,
        retrieved="2026-05-01",
    ))
    assert isinstance(results[0], FetchResult)


def test_batch_no_fetch_errors_when_source_missing(tmp_path):
    config = [{"ctext_juan": 1, "book": "wei", "juan": 1}]
    results = list(batch(
        config,
        fetcher=lambda _: b"unused",
        repo_root=tmp_path,
        no_fetch=True,
        sleeper=lambda _: None,
        retrieved="2026-05-01",
    ))
    assert isinstance(results[0], FetchError)
    assert "does not exist" in results[0].message


def test_batch_continues_after_fetch_error(tmp_path, fixture_bytes):
    config = [
        {"ctext_juan": 1, "book": "wei", "juan": 1},
        {"ctext_juan": 2, "book": "wei", "juan": 2},
    ]

    def flaky(url: str) -> bytes:
        if url.endswith("/卷01"):
            raise OSError("simulated network blip")
        return fixture_bytes

    results = list(batch(
        config,
        fetcher=flaky,
        repo_root=tmp_path,
        sleeper=lambda _: None,
        retrieved="2026-05-01",
    ))
    assert isinstance(results[0], FetchError)
    assert isinstance(results[1], FetchResult)
