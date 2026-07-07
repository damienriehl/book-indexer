"""requirements_addressed: VER-02, VER-03

Unit tests for the positional-window matcher. Uses in-memory SQLite via
Phase 1's _DDL import (same pattern as tests/unit/test_corpus_writer.py).
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import pytest

from book_indexer.ingest.corpus_writer import _DDL
from book_indexer.verify.matcher import MatchHit, scan_matches
from book_indexer.verify.query_tokenizer import QueryToken


# ----- synthetic corpus builder -----

def _insert_page(conn, pdf_page: int, folio: str = "42") -> None:
    conn.execute(
        "INSERT INTO pages (pdf_page, folio, folio_style, folio_tier, "
        "page_section, active_chapter_section, section, bbox_page, block_count) "
        "VALUES (?, ?, 'arabic', 'tier2', NULL, NULL, 'body', '[0,0,540,720]', 1)",
        (pdf_page, folio),
    )


def _insert_section(
    conn, section_id: int, section_ref: str, level: int,
    start_page: int, start_off: int, end_page: int, end_off: int,
    parent_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sections (section_id, section_ref, global_id, section_level, "
        "chapter, parent_id, title, start_pdf_page, start_token_offset, "
        "end_pdf_page, end_token_offset, start_folio) "
        "VALUES (?, ?, ?, ?, 1, ?, 'Test Section', ?, ?, ?, ?, '42')",
        (section_id, section_ref, f"ch1-{section_ref}", level, parent_id,
         start_page, start_off, end_page, end_off),
    )


def _insert_tokens(conn, pdf_page: int, tokens: Iterable[tuple[str, str, str, int | None]]) -> None:
    """tokens: iterable of (text, norm, lemma, section_id). Indices auto-assigned."""
    for i, (text, norm, lemma, sec_id) in enumerate(tokens):
        conn.execute(
            "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, "
            "block_type, block_role, bbox, font_size, font_name, "
            "crosses_page_break, section_id) "
            "VALUES (?, ?, ?, ?, ?, 'body', NULL, '[0,0,0,0]', 10.98, "
            "'CenturySchoolbook', 0, ?)",
            (pdf_page, i, text, norm, lemma, sec_id),
        )


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")  # simplifies test insertion
    conn.executescript(_DDL)
    yield conn
    conn.close()


# ----- exact mode -----

def test_exact_mode_single_token(mem_conn):
    _insert_page(mem_conn, 1, folio="1")
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [
        ("The", "the", "the", 1),
        ("Voir", "voir", "voir", 1),
        ("Dire", "dire", "dire", 1),
        ("panel", "panel", "panel", 1),
    ])
    qs = [QueryToken(norm="voir", lemma="voir"), QueryToken(norm="dire", lemma="dire")]
    hits = list(scan_matches(mem_conn, qs))
    assert len(hits) == 1
    assert hits[0].match_mode == "exact"
    assert hits[0].pdf_page == 1
    assert hits[0].token_start == 1
    assert hits[0].token_end == 2
    assert hits[0].section_id == 1
    assert hits[0].matched_variant == "Voir Dire"


# ----- lemma mode -----

def test_lemma_mode_when_norm_differs(mem_conn):
    """Query "hearsays" should match corpus "hearsay" under lemma mode."""
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [("hearsay", "hearsay", "hearsay", 1)])
    qs = [QueryToken(norm="hearsays", lemma="hearsay")]
    hits = list(scan_matches(mem_conn, qs))
    assert len(hits) == 1
    assert hits[0].match_mode == "lemma"


def test_exact_wins_over_lemma(mem_conn):
    """If a window matches both exact and lemma, Evidence.match_mode == 'exact'."""
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [("hearsay", "hearsay", "hearsay", 1)])
    qs = [QueryToken(norm="hearsay", lemma="hearsay")]  # both match
    hits = list(scan_matches(mem_conn, qs))
    assert len(hits) == 1
    assert hits[0].match_mode == "exact"  # exact wins


# ----- acronym mode -----

def test_acronym_mode_with_variants(mem_conn):
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [("FRE", "fre", "fre", 1)])
    # canonical query "Federal Rules of Evidence" won't appear; variant "FRE" will
    qs_canonical = [QueryToken(norm="federal", lemma="federal"),
                    QueryToken(norm="rules", lemma="rule"),
                    QueryToken(norm="of", lemma="of"),
                    QueryToken(norm="evidence", lemma="evidence")]
    variants = [[QueryToken(norm="fre", lemma="fre")]]
    hits = list(scan_matches(mem_conn, qs_canonical, acronym_variants=variants))
    assert len(hits) == 1
    assert hits[0].match_mode == "acronym"
    assert hits[0].matched_variant == "FRE"


# ----- multi-token phrase (VER-03) -----

def test_multi_token_phrase_spans_within_page(mem_conn):
    """Phrase on tokens (3,4,5) — matcher treats it as contiguous even if
    the source PDF wrapped it across lines (token_index is page-local)."""
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [
        ("x", "x", "x", 1), ("y", "y", "y", 1), ("z", "z", "z", 1),
        ("motion", "motion", "motion", 1),
        ("in", "in", "in", 1),
        ("limine", "limine", "limine", 1),
    ])
    qs = [QueryToken(norm="motion", lemma="motion"),
          QueryToken(norm="in", lemma="in"),
          QueryToken(norm="limine", lemma="limine")]
    hits = list(scan_matches(mem_conn, qs))
    assert len(hits) == 1
    assert hits[0].token_start == 3
    assert hits[0].token_end == 5


# ----- VER-03: phrase starting on page N cites page N even if continues to N+1 -----

def test_phrase_crossing_page_boundary_cites_starting_page(mem_conn):
    """Per-page scan never crosses pdf_page — phrase starting on page 1
    is only found if all N tokens fit within page 1."""
    _insert_page(mem_conn, 1)
    _insert_page(mem_conn, 2)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 2, 10)
    # page 1 has only "motion"; "in limine" starts on page 2
    _insert_tokens(mem_conn, 1, [("motion", "motion", "motion", 1)])
    _insert_tokens(mem_conn, 2, [
        ("in", "in", "in", 1), ("limine", "limine", "limine", 1),
    ])
    qs = [QueryToken(norm="motion", lemma="motion"),
          QueryToken(norm="in", lemma="in"),
          QueryToken(norm="limine", lemma="limine")]
    # The 3-token phrase cannot be matched because per-page scan never crosses
    # pdf_page boundaries. This is the correct per-D-16 behavior: the matcher
    # enforces "phrase must fit within the starting page".
    hits = list(scan_matches(mem_conn, qs))
    assert hits == []  # no cross-page phrase match


# ----- body-only filter -----

def test_footnote_tokens_excluded(mem_conn):
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    # Insert a footnote token directly (block_type='footnote')
    mem_conn.execute(
        "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, block_type, "
        "block_role, bbox, font_size, font_name, crosses_page_break, section_id) "
        "VALUES (1, 0, 'hearsay', 'hearsay', 'hearsay', 'footnote', NULL, "
        "'[0,0,0,0]', 8.0, 'CS', 0, NULL)",
    )
    qs = [QueryToken(norm="hearsay", lemma="hearsay")]
    assert list(scan_matches(mem_conn, qs)) == []


def test_null_section_id_tokens_excluded(mem_conn):
    _insert_page(mem_conn, 1)
    _insert_tokens(mem_conn, 1, [("hearsay", "hearsay", "hearsay", None)])
    qs = [QueryToken(norm="hearsay", lemma="hearsay")]
    assert list(scan_matches(mem_conn, qs)) == []


# ----- determinism (D-04) -----

def test_matcher_order_is_pdf_page_then_token_index(mem_conn):
    _insert_page(mem_conn, 1)
    _insert_page(mem_conn, 2)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 2, 10)
    _insert_tokens(mem_conn, 2, [("x", "x", "x", 1), ("hearsay", "hearsay", "hearsay", 1)])
    _insert_tokens(mem_conn, 1, [("y", "y", "y", 1), ("hearsay", "hearsay", "hearsay", 1)])
    qs = [QueryToken(norm="hearsay", lemma="hearsay")]
    hits = list(scan_matches(mem_conn, qs))
    assert [(h.pdf_page, h.token_start) for h in hits] == [(1, 1), (2, 1)]


def test_two_calls_produce_identical_order(mem_conn):
    """D-04 determinism — list equality across repeated calls."""
    _insert_page(mem_conn, 1)
    _insert_section(mem_conn, 1, "§1.01", 2, 1, 0, 1, 10)
    _insert_tokens(mem_conn, 1, [
        ("hearsay", "hearsay", "hearsay", 1),
        ("hearsay", "hearsay", "hearsay", 1),
    ])
    qs = [QueryToken(norm="hearsay", lemma="hearsay")]
    a = list(scan_matches(mem_conn, qs))
    b = list(scan_matches(mem_conn, qs))
    assert a == b


def test_empty_query_returns_empty(mem_conn):
    assert list(scan_matches(mem_conn, [])) == []
