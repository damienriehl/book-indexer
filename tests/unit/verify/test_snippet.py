"""requirements_addressed: VER-04 (snippet component)

Unit tests for the snippet builder.
"""
from __future__ import annotations

import sqlite3

import pytest

from book_indexer.ingest.corpus_writer import _DDL
from book_indexer.verify.errors import VerifierError
from book_indexer.verify.snippet import build_snippet


def _insert_page(conn, pdf_page: int) -> None:
    conn.execute(
        "INSERT INTO pages (pdf_page, folio, folio_style, folio_tier, "
        "page_section, active_chapter_section, section, bbox_page, block_count) "
        "VALUES (?, '1', 'arabic', 'tier2', NULL, NULL, 'body', '[0,0,540,720]', 1)",
        (pdf_page,),
    )


def _insert_tokens(conn, pdf_page: int, texts: list[str]) -> None:
    for i, t in enumerate(texts):
        conn.execute(
            "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, "
            "block_type, block_role, bbox, font_size, font_name, "
            "crosses_page_break, section_id) "
            "VALUES (?, ?, ?, ?, ?, 'body', NULL, '[0,0,0,0]', 10.98, 'CS', 0, NULL)",
            (pdf_page, i, t, t.lower(), t.lower()),
        )


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_DDL)
    yield conn
    conn.close()


def test_snippet_meets_60_char_floor(mem_conn):
    _insert_page(mem_conn, 1)
    words = ["The", "Court", "held", "that", "voir", "dire", "is", "an",
             "essential", "procedural", "safeguard", "under", "the",
             "Sixth", "Amendment", "due", "process", "clause",
             "notwithstanding", "other", "contrary", "authority"]
    _insert_tokens(mem_conn, 1, words)
    s = build_snippet(mem_conn, pdf_page=1, token_start=4, token_end=5)
    assert len(s) >= 60
    assert "voir dire" in s.lower()


def test_snippet_uses_text_not_norm(mem_conn):
    """Surface form (Text) appears in snippet, not the lowercased norm."""
    _insert_page(mem_conn, 1)
    _insert_tokens(mem_conn, 1, ["The", "MOTION", "In", "LIMINE", "was",
                                  "denied", "by", "the", "district", "court",
                                  "in", "accordance", "with", "precedent",
                                  "established", "earlier"])
    s = build_snippet(mem_conn, pdf_page=1, token_start=1, token_end=3)
    assert "MOTION" in s  # uppercase preserved (tokens.text, not tokens.norm)


def test_snippet_does_not_cross_page_boundary(mem_conn):
    """Pitfall 6: ±30 window clips at pdf_page boundary — never bleeds to next page."""
    _insert_page(mem_conn, 1)
    _insert_page(mem_conn, 2)
    _insert_tokens(mem_conn, 1, ["voir", "dire"] + ["x"] * 60)  # 62 tokens
    _insert_tokens(mem_conn, 2, ["next_page_token"] * 60)
    s = build_snippet(mem_conn, pdf_page=1, token_start=0, token_end=1)
    assert "next_page_token" not in s


def test_snippet_raises_when_page_too_short(mem_conn):
    """If ±50 tokens on the page still yields <60 chars, raise VerifierError."""
    _insert_page(mem_conn, 1)
    _insert_tokens(mem_conn, 1, ["a", "b"])  # only 2 tokens; joined = "a b" = 3 chars
    with pytest.raises(VerifierError, match=">=60-char snippet|60-char"):
        build_snippet(mem_conn, pdf_page=1, token_start=0, token_end=1)
