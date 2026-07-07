"""requirements_addressed: VER-04 (section_path component)

Unit tests for the parent-walk resolver. Uses in-memory SQLite (_DDL import)
matching the pattern in tests/unit/verify/test_matcher.py.
"""
from __future__ import annotations

import sqlite3

import pytest

from book_indexer.ingest.corpus_writer import _DDL
from book_indexer.verify.errors import VerifierError
from book_indexer.verify.section_path import resolve_section_path


def _insert_section(
    conn, section_id: int, section_ref: str, level: int,
    parent_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sections (section_id, section_ref, global_id, section_level, "
        "chapter, parent_id, title, start_pdf_page, start_token_offset, "
        "end_pdf_page, end_token_offset, start_folio) "
        "VALUES (?, ?, ?, ?, 1, ?, 't', 1, 0, 999, 999, '0')",
        (section_id, section_ref, f"x-{section_id}", level, parent_id),
    )


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_DDL)
    yield conn
    conn.close()


def test_level_1_path_is_one_deep(mem_conn):
    _insert_section(mem_conn, 1, "Chapter 1", 0)
    _insert_section(mem_conn, 2, "§1", 1, parent_id=1)
    assert resolve_section_path(mem_conn, 2) == ["§1"]


def test_level_2_path_is_two_deep(mem_conn):
    _insert_section(mem_conn, 1, "Chapter 1", 0)
    _insert_section(mem_conn, 2, "§1", 1, parent_id=1)
    _insert_section(mem_conn, 3, "§1.01", 2, parent_id=2)
    assert resolve_section_path(mem_conn, 3) == ["§1", "§1.01"]


def test_level_3_path_is_three_deep(mem_conn):
    _insert_section(mem_conn, 1, "Chapter 1", 0)
    _insert_section(mem_conn, 2, "§1", 1, parent_id=1)
    _insert_section(mem_conn, 3, "§1.01", 2, parent_id=2)
    _insert_section(mem_conn, 4, "§1.01.1", 3, parent_id=3)
    assert resolve_section_path(mem_conn, 4) == ["§1", "§1.01", "§1.01.1"]


def test_level_0_chapter_is_excluded(mem_conn):
    """RESEARCH §Pitfall 8: level-0 never appears in section_path."""
    _insert_section(mem_conn, 1, "Chapter 1", 0)
    _insert_section(mem_conn, 2, "§1", 1, parent_id=1)
    path = resolve_section_path(mem_conn, 2)
    assert "Chapter 1" not in path
    assert path == ["§1"]


def test_missing_section_id_returns_empty(mem_conn):
    assert resolve_section_path(mem_conn, 999) == []


def test_cycle_raises_verifier_error(mem_conn):
    _insert_section(mem_conn, 1, "§1", 1, parent_id=2)
    _insert_section(mem_conn, 2, "§1.01", 2, parent_id=1)  # cycle
    with pytest.raises(VerifierError, match="cycle"):
        resolve_section_path(mem_conn, 1)
