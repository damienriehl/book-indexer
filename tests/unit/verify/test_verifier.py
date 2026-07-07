"""requirements_addressed: VER-01, VER-02, VER-03

Integration unit test for verify() — end-to-end composition of matcher +
section_path + snippet + Evidence construction. Uses synthetic in-memory
SQLite via _DDL import (same pattern as tests/unit/verify/test_matcher.py).
Hypothesis property coverage lives in tests/property/ (Plan 02-03).
"""
from __future__ import annotations

import sqlite3

import pytest

from book_indexer.ingest.corpus_writer import _DDL
from book_indexer.verify import Evidence, verify


def _build_synthetic(folio: str = "42") -> sqlite3.Connection:
    """Build a 1-page, 1-section synthetic corpus with readable prose."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO pages (pdf_page, folio, folio_style, folio_tier, "
        "page_section, active_chapter_section, section, bbox_page, block_count) "
        "VALUES (1, ?, 'arabic', 'tier2', NULL, NULL, 'body', '[0,0,540,720]', 1)",
        (folio,),
    )
    # Chapter(level 0) → §1(level 1) → §1.01(level 2)
    conn.execute(
        "INSERT INTO sections VALUES "
        "(1, 'Chapter 1', 'ch1-Chapter 1', 0, 1, NULL, 'Chapter Title', 1, 0, 99, 999, '42'),"
        "(2, '§1', 'ch1-§1', 1, 1, 1, 'First Chapter-Section', 1, 0, 99, 999, '42'),"
        "(3, '§1.01', 'ch1-§1.01', 2, 1, 2, 'Voir Dire', 1, 0, 99, 999, '42')"
    )
    # Prose: enough surrounding tokens so a +/-30 snippet hits >=60 chars.
    # The legal-phrase-merger in ingest/tokenizer merges multi-word legal
    # phrases like "voir dire" into a SINGLE token with norm="voir dire".
    # Our synthetic corpus mirrors that — one merged "Voir Dire" token at
    # position 6, surrounded by ordinary tokens. query_tokenizer produces
    # the same single-token form, so the matcher can exact-match it.
    rows: list[tuple[str, str, str]] = []  # (text, norm, lemma)
    for w in "The court addressed the matter of".split():
        rows.append((w, w.lower(), w.lower()))
    rows.append(("Voir Dire", "voir dire", "voir dire"))  # merged phrase token
    for w in ("procedure in considerable detail notwithstanding prior authority "
              "establishing a contrary rule for selection of the jury panel").split():
        rows.append((w, w.lower(), w.lower()))
    for i, (text, norm, lemma) in enumerate(rows):
        conn.execute(
            "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, block_type, "
            "block_role, bbox, font_size, font_name, crosses_page_break, section_id) "
            "VALUES (1, ?, ?, ?, ?, 'body', NULL, '[0,0,0,0]', 10.98, 'CS', 0, 3)",
            (i, text, norm, lemma),
        )
    return conn


def test_verify_yields_evidence_for_known_term():
    conn = _build_synthetic()
    results = list(verify("voir dire", conn))
    assert len(results) == 1
    ev = results[0]
    assert isinstance(ev, Evidence)
    assert ev.canonical_term == "voir dire"
    assert ev.section_ref == "§1.01"
    assert ev.section_level == 2
    assert ev.section_path == ("§1", "§1.01")
    assert ev.folio == "42"
    assert ev.match_mode == "exact"
    assert len(ev.verbatim_snippet) >= 60
    assert "voir dire" in ev.verbatim_snippet.lower()


def test_verify_raises_on_empty_term():
    conn = _build_synthetic()
    with pytest.raises(ValueError, match="non-empty"):
        list(verify("", conn))
    with pytest.raises(ValueError, match="non-empty"):
        list(verify("   ", conn))


def test_verify_returns_empty_for_absent_term():
    conn = _build_synthetic()
    assert list(verify("xyzzy_sentinel_42", conn)) == []


def test_verify_deterministic_two_calls():
    """D-04 contract: list(verify(t,c)) == list(verify(t,c))."""
    conn = _build_synthetic()
    a = list(verify("voir dire", conn))
    b = list(verify("voir dire", conn))
    assert a == b
    # Evidence is frozen+hashable → set membership works:
    assert set(a) == set(b)


def test_verify_accepts_variants_for_callable():
    """Acronym mode — variants_for callable provides alternate surface forms."""
    conn = _build_synthetic()

    def variants(_term: str) -> list[str]:
        return []  # no variants for voir dire → same result as None

    r1 = list(verify("voir dire", conn))
    r2 = list(verify("voir dire", conn, variants_for=variants))
    assert r1 == r2


def test_verify_skips_section_paths_with_null_parent():
    """Defense in depth: hits whose sections lack a section_path are skipped."""
    # Build a corpus where section_id=99 has no entry in sections → resolve_section_path → []
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO pages (pdf_page, folio, folio_style, folio_tier, "
        "page_section, active_chapter_section, section, bbox_page, block_count) "
        "VALUES (1, '42', 'arabic', 'tier2', NULL, NULL, 'body', '[0,0,540,720]', 1)"
    )
    # Tokens reference section_id=99 which does not exist → resolve_section_path returns []
    for i, w in enumerate(["hearsay"] * 10):
        conn.execute(
            "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, block_type, "
            "block_role, bbox, font_size, font_name, crosses_page_break, section_id) "
            "VALUES (1, ?, ?, ?, ?, 'body', NULL, '[0,0,0,0]', 10.98, 'CS', 0, 99)",
            (i, w, w, w),
        )
    assert list(verify("hearsay", conn)) == []
