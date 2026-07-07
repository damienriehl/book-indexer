"""Verify-shim layer between extractor RawHits and Phase 2 ``verify()``.

This module is the SOLE consumer of ``verify()`` in Phase 3b. Architecture
Lock #1 holds: this module does NOT construct ``Evidence``. It only calls
Phase 2 ``verify()`` and returns the iterator's contents.

Per RESEARCH §H-3 (Phase 3b):

* ``verify_case``    — passes the full display_name (multi-token); Phase 2's
                       positional matcher handles "Jones v. Barnes" as 4 tokens.
* ``verify_statute`` — tries BOTH canonical and surface phrasings to handle
                       Pitfall P-5 (``Sec.`` vs ``§`` surface forms). De-dups
                       by ``(pdf_page, token_offset)``.
* ``verify_rule``    — passes the BARE PARENT rule only (e.g. ``FRE 404``,
                       NEVER ``FRE 404(b)``) because Phase 1's tokenizer
                       fuses ``404(b`` into one token (Pitfall P-2). Subsection
                       narrowing is the CALLER's job (in ``__main__.py``,
                       based on char-offset proximity to the regex_fallback hit).

requirements_addressed: TAB-04 (every Locator's evidence_id traces to a
verify()-emitted Evidence row).
"""
from __future__ import annotations

import sqlite3

from book_indexer.verify.evidence import Evidence
from book_indexer.verify.verifier import verify


def _materialize_sorted(evidence_iter) -> list[Evidence]:
    """Collect verify()'s iterator into a list sorted by (page, offset).

    Phase 2's verify() yields in (pdf_page ASC, token_offset ASC) order
    already; we re-affirm the ordering after materialization so callers
    that splice multiple iterators (verify_statute) get a stable result.
    """
    rows = list(evidence_iter)
    # Use the Pydantic-emitted attribute names verbatim. Lock #1's AST
    # scanner forbids ``pdf_page`` as a kwarg/dict-key/Name in this file,
    # but attribute reads (``e.pdf_page``) are explicitly allowed — the
    # scanner only flags constructions, not reads.
    rows.sort(key=lambda e: (e.pdf_page, e.token_offset))
    return rows


def verify_case(display_name: str, conn: sqlite3.Connection) -> list[Evidence]:
    """Verify every appearance of a case display_name in the corpus.

    Returns Evidence rows in (pdf_page ASC, token_offset ASC) order.
    Empty input → ``[]`` (sentinel for "skip this hit downstream").
    """
    if not display_name or not display_name.strip():
        return []
    return _materialize_sorted(verify(display_name, conn))


def verify_statute(
    canonical: str,
    surface: str,
    conn: sqlite3.Connection,
) -> list[Evidence]:
    """Verify a statute under BOTH canonical and surface phrasings.

    Pitfall P-5: eyecite's ``corrected_citation()`` does NOT normalize
    ``Sec.`` to ``§`` (e.g., ``28 U.S.C. Sec. 1407`` stays as-is). We must
    look for both phrasings on the corpus to capture every appearance.

    De-dup is by ``(e.pdf_page, e.token_offset)`` — the same physical hit
    surfaces twice when the canonical and surface tokenizations overlap.
    """
    canonical = (canonical or "").strip()
    surface = (surface or "").strip()
    seen: set[tuple[int, int]] = set()
    rows: list[Evidence] = []

    if canonical:
        for ev in verify(canonical, conn):
            key = (ev.pdf_page, ev.token_offset)
            if key not in seen:
                seen.add(key)
                rows.append(ev)

    if surface and surface != canonical:
        for ev in verify(surface, conn):
            key = (ev.pdf_page, ev.token_offset)
            if key not in seen:
                seen.add(key)
                rows.append(ev)

    rows.sort(key=lambda e: (e.pdf_page, e.token_offset))
    return rows


def verify_rule(parent_rule: str, conn: sqlite3.Connection) -> list[Evidence]:
    """Verify the BARE PARENT rule (NO parenthetical subsection).

    Per Pitfall P-2 (RESEARCH §H-3), Phase 1's tokenizer fuses
    parenthesized subsections into the rule-number token. Passing
    ``FRE 404(b)`` to ``verify()`` therefore yields 0 hits even when the
    rule appears throughout the corpus.

    Callers MUST pass the bare parent (e.g., ``FRE 404``); subsection
    narrowing happens at the IR-construction layer (``__main__.py``) by
    matching evidence char-offsets against the regex_fallback hit's
    surface_form span on the same pdf_page.

    Defensive contract: if the caller accidentally passes a parenthetical
    form, raise ``ValueError`` to surface the boundary violation rather
    than silently yielding ``[]``.
    """
    parent = (parent_rule or "").strip()
    if not parent:
        return []
    if "(" in parent:
        raise ValueError(
            f"verify_rule received a parenthetical-subsection form "
            f"{parent!r}; callers must pass the BARE PARENT only "
            "(Pitfall P-2 / RESEARCH §H-3 — Phase 1 tokenizer fuses "
            "'(b' into the rule-number token)."
        )
    return _materialize_sorted(verify(parent, conn))
