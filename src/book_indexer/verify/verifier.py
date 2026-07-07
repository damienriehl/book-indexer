"""Public verify() — the sole locator-emitting function in the project.

Sequence (per call to verify(term, conn, *, variants_for=None)):
  1. Validate input — empty/whitespace term raises ValueError.
  2. Tokenize the query via Phase 1's shared normalize() + nlp_call()
     (query_tokenizer; NEVER re-implement — see RESEARCH Pitfall 1).
  3. If variants_for is provided, tokenize each variant the same way.
  4. Scan corpus `tokens` page-by-page via matcher.scan_matches; SQL orders
     by (pdf_page ASC, token_index ASC).
  5. For each hit: walk sections.parent_id to build section_path, SELECT
     pages.folio, build a >=60-char snippet on the match's pdf_page only.
  6. Construct and yield an Evidence; its Pydantic cross-field validator
     enforces len(section_path)==section_level and section_path[-1]==section_ref.

Iterator order is a project contract (D-04): callers may materialize the
iterator to a list and compare byte-for-byte across runs — the ordering is
deterministic. Do NOT sort downstream; that risks introducing drift.

This is the ONLY code path in src/book_indexer/ that constructs an
Evidence. Architecture Lock #1 is CI-enforced by
tests/invariants/test_verify_is_sole_locator_source.py (Plan 02-04).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

from .evidence import Evidence
from .matcher import scan_matches
from .query_tokenizer import tokenize_query
from .section_path import resolve_section_path
from .snippet import build_snippet


def verify(
    term: str,
    conn: sqlite3.Connection,
    *,
    variants_for: Callable[[str], list[str]] | None = None,
) -> Iterator[Evidence]:
    """Symbolically verify every occurrence of ``term`` in the corpus.

    Yields Evidence in (pdf_page ASC, token_offset ASC) order. The iterator
    order is a project contract (D-04); do NOT re-sort downstream.

    Args:
        term: the canonical term to verify. Must not be empty or whitespace.
        conn: read-only SQLite connection to page_corpus.sqlite (or an
              in-memory copy via Connection.backup()).
        variants_for: optional callable returning acronym variants for the
                      canonical term. If None, acronym-mode matching is
                      disabled. Phase 4's sweep injects this; Phase 2 tests
                      pass None or a fixture-driven map.

    Returns:
        Iterator[Evidence]. An empty iterator means "term does not occur in
        the corpus under any match mode."

    Raises:
        ValueError: if ``term`` is empty or whitespace-only.
        pydantic.ValidationError: if the matcher produces a hit whose
                                  enriched form fails Evidence validation —
                                  indicates matcher/schema drift.
        VerifierError: if section_path walk or snippet build fails.
    """
    if not term or not term.strip():
        raise ValueError("term must be non-empty")

    query_tokens = tokenize_query(term)
    if not query_tokens:
        return

    acronym_variants: list = []
    if variants_for is not None:
        for v in variants_for(term):
            vt = tokenize_query(v)
            if vt:
                acronym_variants.append(vt)

    for hit in scan_matches(conn, query_tokens, acronym_variants or None):
        section_path = resolve_section_path(conn, hit.section_id)
        if not section_path:
            # Defense in depth for VER-05(c): skip hits that lack a section.
            continue

        pdf_page_row = conn.execute(
            "SELECT folio FROM pages WHERE pdf_page = ?",
            (hit.pdf_page,),
        ).fetchone()
        folio = (pdf_page_row[0] if pdf_page_row else "") or ""

        snippet = build_snippet(
            conn,
            pdf_page=hit.pdf_page,
            token_start=hit.token_start,
            token_end=hit.token_end,
        )

        yield Evidence(
            canonical_term=term,
            matched_variant=hit.matched_variant,
            section_ref=section_path[-1],
            section_level=len(section_path),
            section_path=tuple(section_path),
            folio=folio,
            pdf_page=hit.pdf_page,
            token_offset=hit.token_start,
            match_mode=hit.match_mode,
            verbatim_snippet=snippet,
        )
