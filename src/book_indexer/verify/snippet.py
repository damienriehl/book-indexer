"""Snippet builder — ±30 tokens around the match, ≥60 chars, single page only.

Pitfall 6 (RESEARCH): the ±30-token window NEVER crosses pdf_page. If the
match is near a page boundary, the window clips at the page boundary; if the
resulting snippet is <60 chars, the builder widens to ±50 tokens (still on
the same page only) before raising. The Evidence Pydantic model enforces the
60-char floor — this builder targets it by construction.

Uses `tokens.text` (surface form) joined with single spaces — NOT `norm` —
so the snippet reads naturally for human reviewers.
"""
from __future__ import annotations

import sqlite3

from .errors import VerifierError

_WINDOW_DEFAULT = 30
_WINDOW_WIDE = 50
_MIN_CHARS = 60


def build_snippet(
    conn: sqlite3.Connection,
    *,
    pdf_page: int,
    token_start: int,
    token_end: int,
) -> str:
    """Build a >=60-char snippet for the match at (pdf_page, token_start..token_end).

    Strategy:
      1. Fetch all tokens on `pdf_page` whose token_index is within
         [token_start - 30, token_end + 30].
      2. Join their `text` fields with single spaces.
      3. If the result is <60 chars, widen to +/-50 and retry (still same page).
      4. If still <60 chars, raise VerifierError — the developer can then
         decide to skip the match or log it.
    """
    for window in (_WINDOW_DEFAULT, _WINDOW_WIDE):
        low = max(0, token_start - window)
        high = token_end + window
        rows = conn.execute(
            "SELECT text FROM tokens "
            "WHERE pdf_page = ? AND token_index BETWEEN ? AND ? "
            "ORDER BY token_index ASC",
            (pdf_page, low, high),
        ).fetchall()
        snippet = " ".join(r[0] for r in rows).strip()
        if len(snippet) >= _MIN_CHARS:
            return snippet

    raise VerifierError(
        f"could not build >=60-char snippet on pdf_page={pdf_page} "
        f"(token_start={token_start}, token_end={token_end}) without crossing a page boundary"
    )
