"""Unit tests for ``book_indexer.tables.resolver``.

The resolver is a per-chapter eyecite ``resolve_citations`` wrapper.
Per CONTEXT D-08, eyecite resolves Id./Supra. only within a chapter
boundary; orphan short cites (no preceding full cite within the same
chapter) are logged to provenance and dropped from the final tables.

Test surface:
* Resolved Id. with preceding full cite → in resolved dict; not unresolved.
* Orphan Id./Supra. (no full cite) → unresolved record with kind set.
* Empty / no-citation text → ``({}, [])``.
* Per-chapter scope: state does NOT leak across two ``resolve_chapter`` calls.
* ``UnresolvedCiteRecord`` is a frozen dataclass (no mutation).
"""
from __future__ import annotations

import pytest


def test_resolve_chapter_resolves_id_with_preceding_full_cite() -> None:
    from book_indexer.tables.resolver import resolve_chapter
    text = "Smith v. Jones, 410 U.S. 113 (1973). Id. at 119."
    resolved, unresolved = resolve_chapter(text, chunk_id="ch1", base_pdf_page=42)
    assert len(resolved) == 1, "one resolved group (the FullCaseCitation + Id.)"
    assert unresolved == []


def test_resolve_chapter_orphan_id_unresolved() -> None:
    """Id. with NO preceding full cite within this chapter → unresolved."""
    from book_indexer.tables.resolver import resolve_chapter
    text = "Id. at 92."
    resolved, unresolved = resolve_chapter(text, chunk_id="ch3", base_pdf_page=140)
    assert resolved == {}
    assert len(unresolved) == 1
    assert unresolved[0].kind == "IdCitation"
    assert unresolved[0].chunk_id == "ch3"
    assert unresolved[0].pdf_page == 140


def test_resolve_chapter_orphan_supra_unresolved() -> None:
    """Supra. with no preceding full cite → unresolved."""
    from book_indexer.tables.resolver import resolve_chapter
    text = "See supra note 3."
    resolved, unresolved = resolve_chapter(text, chunk_id="ch2", base_pdf_page=96)
    assert resolved == {}
    assert len(unresolved) == 1
    assert unresolved[0].kind == "SupraCitation"
    assert unresolved[0].chunk_id == "ch2"


def test_resolve_chapter_empty_text() -> None:
    from book_indexer.tables.resolver import resolve_chapter
    assert resolve_chapter("", chunk_id="ch1") == ({}, [])


def test_resolve_chapter_no_citations() -> None:
    from book_indexer.tables.resolver import resolve_chapter
    text = "the quick brown fox jumps over the lazy dog"
    resolved, unresolved = resolve_chapter(text, chunk_id="ch1")
    assert resolved == {}
    assert unresolved == []


def test_resolve_chapter_state_does_not_leak_across_calls() -> None:
    """D-08: per-chapter scope. Calling resolve_chapter twice with
    independent texts must not let chapter A's full cite resolve
    chapter B's orphan Id."""
    from book_indexer.tables.resolver import resolve_chapter

    # Chapter A: full cite establishes a resource.
    text_a = "Smith v. Jones, 410 U.S. 113 (1973)."
    resolved_a, unresolved_a = resolve_chapter(text_a, chunk_id="chA")
    assert len(resolved_a) == 1
    assert unresolved_a == []

    # Chapter B: orphan Id. — must NOT resolve via chapter A's state.
    text_b = "Id. at 200."
    resolved_b, unresolved_b = resolve_chapter(text_b, chunk_id="chB")
    assert resolved_b == {}
    assert len(unresolved_b) == 1
    assert unresolved_b[0].kind == "IdCitation"
    assert unresolved_b[0].chunk_id == "chB"


def test_unresolved_record_carries_chunk_id_and_offset() -> None:
    from book_indexer.tables.resolver import resolve_chapter
    text = "    Id. at 100."  # leading whitespace shifts char offset
    _, unresolved = resolve_chapter(text, chunk_id="my-chunk", base_pdf_page=7)
    assert len(unresolved) == 1
    rec = unresolved[0]
    assert rec.chunk_id == "my-chunk"
    assert rec.pdf_page == 7
    assert rec.char_offset >= 4  # after the leading whitespace
    assert rec.matched_text  # non-empty


def test_unresolved_record_is_frozen() -> None:
    """UnresolvedCiteRecord must be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    from book_indexer.tables.resolver import UnresolvedCiteRecord
    rec = UnresolvedCiteRecord(
        chunk_id="x", pdf_page=1, char_offset=0,
        matched_text="Id.", kind="IdCitation",
    )
    with pytest.raises(FrozenInstanceError):
        rec.chunk_id = "y"  # type: ignore[misc]


def test_resolve_chapter_full_cite_alone() -> None:
    """A standalone FullCaseCitation produces 1 resolved group + 0 unresolved."""
    from book_indexer.tables.resolver import resolve_chapter
    text = "Roe v. Wade, 410 U.S. 113 (1973)."
    resolved, unresolved = resolve_chapter(text, chunk_id="ch1")
    assert len(resolved) == 1
    assert unresolved == []
