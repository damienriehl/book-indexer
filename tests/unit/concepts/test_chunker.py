"""Unit tests for ``src/book_indexer/concepts/chunker.py``.

Targets H-4 determinism (byte-identical ``chunk_text`` across runs) and the
read-only corpus guards. The structural-invariant tests that required the
full reference corpus (a private asset) are exercised in the source repo;
here we keep only the self-contained tests plus the ones that gracefully skip
when ``artifacts/page_corpus.sqlite`` is absent.

requirements_addressed: CON-03, CON-06
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from book_indexer.concepts.chunker import (
    build_chunk_text,
    open_read_only_corpus,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORPUS_PATH = _REPO_ROOT / "artifacts" / "page_corpus.sqlite"


# ---------------------------------------------------------------------------
# H-4 determinism lock (skips cleanly when the live corpus is absent)
# ---------------------------------------------------------------------------


def test_chunk_text_byte_identical_across_fresh_connections() -> None:
    """Stronger H-4: byte-identical across FRESH connections (same corpus path).

    Catches any hidden state in the ``sqlite3.Connection`` (PRAGMAs, cached
    statements) that could perturb query results between opens.
    """
    if not _CORPUS_PATH.exists():
        pytest.skip(f"live corpus not built: {_CORPUS_PATH}")
    conn_a = open_read_only_corpus(_CORPUS_PATH)
    try:
        text_a = build_chunk_text(conn_a, 2)
    finally:
        conn_a.close()
    conn_b = open_read_only_corpus(_CORPUS_PATH)
    try:
        text_b = build_chunk_text(conn_b, 2)
    finally:
        conn_b.close()
    assert text_a == text_b


# ---------------------------------------------------------------------------
# Read-only guard
# ---------------------------------------------------------------------------


def test_open_read_only_corpus_rejects_writes() -> None:
    """PRAGMA query_only = 1 → writes raise OperationalError."""
    if not _CORPUS_PATH.exists():
        pytest.skip(f"live corpus not built: {_CORPUS_PATH}")
    conn = open_read_only_corpus(_CORPUS_PATH)
    try:
        with pytest.raises(sqlite3.OperationalError) as exc:
            conn.execute(
                "INSERT INTO tokens "
                "(pdf_page, token_index, text, norm, lemma, block_type, "
                " bbox, font_size, font_name, crosses_page_break) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (999, 0, "rogue", "rogue", "rogue", "body",
                 "[0,0,0,0]", 10.0, "CS", 0),
            )
        # Error message varies by SQLite version ("attempt to write a readonly
        # database" on some, "query_only" on others). Match either.
        msg = str(exc.value).lower()
        assert "read" in msg or "query" in msg, (
            f"unexpected write-rejection msg: {msg}"
        )
    finally:
        conn.close()


def test_open_read_only_corpus_missing_file_raises(tmp_path: Path) -> None:
    """A missing corpus path raises ``FileNotFoundError`` — not a cryptic
    sqlite error from trying to open a nonexistent DB."""
    with pytest.raises(FileNotFoundError):
        open_read_only_corpus(tmp_path / "nonexistent.sqlite")
