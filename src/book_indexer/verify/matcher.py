"""Positional-window matcher — the core scan of verify() (VER-02, VER-03).

Reads `tokens` + `sections` read-only; never writes. Three match modes try
in precedence order: exact (norm) > lemma > acronym (variant norm). The
strongest mode that fits a window wins; the scan moves on to the next
starting position as soon as one mode fires.

Variable-length acronym variants: the canonical query may be N tokens
("Federal Rules of Evidence" = 4) but an acronym variant is 1 token
("FRE"). The matcher therefore considers each (mode, length) independently
at every starting position, always preferring the strongest mode that
matches. The starting position advances by 1 regardless of which mode
fired — consistent with the (pdf_page ASC, token_index ASC) contract.

Determinism (D-04/D-05): the per-page scan is `ORDER BY pdf_page ASC,
token_index ASC` at the SQL level. Do NOT re-sort in Python — sorting in
Python is a code smell that invites sort-key drift.

Body-only filter (VER-05(c) + Phase 1 D-24): `block_type='body' AND
section_id IS NOT NULL`. Footnote / header_footer / pre-§-1 tokens have
section_id=NULL and are excluded; emission outside a section's token range
is forbidden.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from .query_tokenizer import QueryToken


@dataclass(frozen=True, slots=True)
class MatchHit:
    """One candidate match before section_path / snippet / folio enrichment."""
    pdf_page: int
    token_start: int
    token_end: int
    section_id: int
    match_mode: str       # "exact" | "lemma" | "acronym"
    matched_variant: str  # surface form joined by spaces from tokens.text


def scan_matches(
    conn: sqlite3.Connection,
    query_tokens: list[QueryToken],
    acronym_variants: list[list[QueryToken]] | None = None,
) -> Iterator[MatchHit]:
    """Yield every (body, section-scoped) match of query_tokens in corpus order."""
    if not query_tokens:
        return

    n = len(query_tokens)
    variants = acronym_variants or []

    # Shortest window that can fire at all — lets us skip pages that are too
    # short for any mode to match. With no variants, min_len == n (canonical).
    min_len = min([n] + [len(av) for av in variants if av])

    page_rows = conn.execute(
        "SELECT DISTINCT pdf_page FROM tokens "
        "WHERE block_type='body' AND section_id IS NOT NULL "
        "ORDER BY pdf_page ASC"
    ).fetchall()

    for (pdf_page,) in page_rows:
        tokens = conn.execute(
            "SELECT token_index, norm, lemma, text, section_id "
            "FROM tokens "
            "WHERE pdf_page=? AND block_type='body' AND section_id IS NOT NULL "
            "ORDER BY token_index ASC",
            (pdf_page,),
        ).fetchall()
        if len(tokens) < min_len:
            continue

        page_len = len(tokens)
        for i in range(page_len):
            # Exact (norm) — canonical-length window.
            if i + n <= page_len:
                window = tokens[i : i + n]
                if all(q.norm == w[1] for q, w in zip(query_tokens, window)):
                    yield MatchHit(
                        pdf_page=pdf_page,
                        token_start=window[0][0],
                        token_end=window[-1][0],
                        section_id=window[0][4],
                        match_mode="exact",
                        matched_variant=" ".join(w[3] for w in window),
                    )
                    continue

                # Lemma — canonical-length window, only if norm didn't already
                # win (would have been "exact" above). Duplicate mode collapse:
                # emit one Evidence per start position, strongest mode wins.
                if all(q.lemma == w[2] for q, w in zip(query_tokens, window)):
                    yield MatchHit(
                        pdf_page=pdf_page,
                        token_start=window[0][0],
                        token_end=window[-1][0],
                        section_id=window[0][4],
                        match_mode="lemma",
                        matched_variant=" ".join(w[3] for w in window),
                    )
                    continue

            # Acronym — each variant at its own length.
            matched = False
            for av in variants:
                m = len(av)
                if m == 0 or i + m > page_len:
                    continue
                av_window = tokens[i : i + m]
                if all(q.norm == w[1] for q, w in zip(av, av_window)):
                    yield MatchHit(
                        pdf_page=pdf_page,
                        token_start=av_window[0][0],
                        token_end=av_window[-1][0],
                        section_id=av_window[0][4],
                        match_mode="acronym",
                        matched_variant=" ".join(w[3] for w in av_window),
                    )
                    matched = True
                    break
            if matched:
                continue
