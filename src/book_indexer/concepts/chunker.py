"""Corpus-backed chunk builder for the Phase 3a concept-discovery pipeline.

Produces 5 ``Chunk`` objects (one per chapter — "§ N chapter-section" in D-06
author terminology, stored as ``section_level = 0`` in the Phase 1 corpus)
whose ``chunk_text`` is the deterministic body-prose substrate fed to
``claude -p --json-schema``. The SHA-256 of ``chunk_text`` is a component of
the content-addressed cache key (CON-06 / D-14); any whitespace drift here
busts every cache entry on every machine (H-4).

Contract (03A-CONTEXT.md):

- **D-06**: chunk unit = one chapter ("§ N chapter-section" in author-speak;
  ``sections.section_level = 0`` in the Phase 1 schema). 5 chunks for
  the source book, one per chapter 1..5.
- **D-07**: each chunk's prompt includes the last ``N.NN`` of the prior
  chapter (prepended) + the first ``N.NN`` of the next chapter (appended).
  ``ch1`` has no prior; ``ch5`` has no forward. "N.NN" maps to
  ``sections.section_level = 2`` in this schema.
- **D-16**: body tokens only (``block_type='body'``), ordered by
  ``(pdf_page ASC, token_index ASC)``. Exclude footnotes, header/footer,
  and ``chapter_title`` role blocks.

Determinism hooks (H-4):

- ``open_read_only_corpus`` applies ``PRAGMA query_only = 1`` in addition
  to the Phase 1 determinism PRAGMAs.
- Token join uses ``" "`` within a contiguous token run and ``"\\n\\n"``
  between detected paragraph breaks (page boundary OR ``token_index`` gap).
- Every line is stripped of trailing whitespace before concatenation; no
  BOM; LF-only.

requirements_addressed: CON-03, CON-06
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Chunk",
    "build_all_chunks",
    "build_chunk_text",
    "open_read_only_corpus",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """One chapter (D-06 chunk unit) plus its D-07 semantic-overlap context.

    ``chunk_text`` is the exact payload that goes into the LLM prompt body;
    its bytes feed the D-14 cache-key sha256. Instances are frozen and
    hashable so callers can use them as dict keys or set members in the
    Plan 03A-07 pass orchestrator.
    """

    chunk_id: str            # format ``ch{N}``, e.g. ``ch1``
    chapter: int             # 1..5 for the reference corpus
    start_pdf_page: int      # chapter start (no overlap)
    end_pdf_page: int        # chapter end (no overlap)
    chunk_text: str          # body + D-07 overlaps; LF-only; deterministic


# ---------------------------------------------------------------------------
# Connection open
# ---------------------------------------------------------------------------


def open_read_only_corpus(path: Path) -> sqlite3.Connection:
    """Open ``artifacts/page_corpus.sqlite`` read-only with determinism PRAGMAs.

    Mirrors Phase 1's ``open_deterministic`` (src/book_indexer/ingest/
    corpus_writer.py:228-248) MINUS the ``path.unlink()`` side effect (the
    corpus is authoritative input, never rebuilt by us), PLUS
    ``PRAGMA query_only = 1`` so any accidental write raises
    ``sqlite3.OperationalError``.
    """
    if not path.exists():
        raise FileNotFoundError(f"corpus not found: {path}")
    conn = sqlite3.connect(str(path), isolation_level=None)
    for pragma in (
        "PRAGMA encoding = 'UTF-8'",
        "PRAGMA foreign_keys = ON",
        "PRAGMA query_only = 1",  # read-only guard
    ):
        conn.execute(pragma)
    return conn


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _chapter_bounds(
    conn: sqlite3.Connection, chapter: int
) -> tuple[int, int, int, int] | None:
    """Return ``(start_pdf_page, start_token_offset, end_pdf_page, end_token_offset)``
    for the ``Chapter {chapter}`` row (``section_level = 0``), or ``None``
    if no such chapter exists.

    D-06 / corpus schema note: Phase 1 stores chapters at ``section_level = 0``
    (one row per chapter: "Chapter 1" .. "Chapter 5"). ``section_level = 1``
    is the book's ``§N`` subdivisions *within* a chapter — NOT chapter bounds.
    """
    row = conn.execute(
        "SELECT start_pdf_page, start_token_offset, end_pdf_page, end_token_offset "
        "FROM sections WHERE section_level = 0 AND chapter = ? "
        "ORDER BY start_pdf_page ASC LIMIT 1",
        (chapter,),
    ).fetchone()
    return tuple(row) if row else None  # type: ignore[return-value]


def _last_major_section_of_chapter(
    conn: sqlite3.Connection, chapter: int
) -> tuple[int, int, int, int] | None:
    """Return bounds of the LAST ``N.NN`` major-section of chapter ``N``
    (``section_level = 2``) for D-07 prior-overlap on chapter ``N+1``.

    Returns ``None`` if the chapter has no level-2 sections (e.g. ch0).
    """
    row = conn.execute(
        "SELECT start_pdf_page, start_token_offset, end_pdf_page, end_token_offset "
        "FROM sections WHERE section_level = 2 AND chapter = ? "
        "ORDER BY start_pdf_page DESC, start_token_offset DESC LIMIT 1",
        (chapter,),
    ).fetchone()
    return tuple(row) if row else None  # type: ignore[return-value]


def _first_major_section_of_chapter(
    conn: sqlite3.Connection, chapter: int
) -> tuple[int, int, int, int] | None:
    """Return bounds of the FIRST ``N.NN`` major-section of chapter ``N``
    (``section_level = 2``) for D-07 forward-overlap on chapter ``N-1``."""
    row = conn.execute(
        "SELECT start_pdf_page, start_token_offset, end_pdf_page, end_token_offset "
        "FROM sections WHERE section_level = 2 AND chapter = ? "
        "ORDER BY start_pdf_page ASC, start_token_offset ASC LIMIT 1",
        (chapter,),
    ).fetchone()
    return tuple(row) if row else None  # type: ignore[return-value]


def _fetch_body_tokens(
    conn: sqlite3.Connection,
    start_pdf_page: int, start_token_offset: int,
    end_pdf_page: int, end_token_offset: int,
) -> list[tuple[int, int, str, str | None]]:
    """Fetch body tokens in the closed range ``[(start_pdf_page, start_token_offset),
    (end_pdf_page, end_token_offset)]`` ordered deterministically.

    Excludes footnotes, header/footer, and ``chapter_title`` role blocks
    (D-16 step 5). Only ``block_type = 'body'`` is queried, which already
    excludes ``block_type`` of ``'footnote'`` and ``'header_footer'``; the
    additional ``block_role != 'chapter_title'`` filter covers the rare
    case where a chapter running-head leaked through as body.

    Returns rows of ``(pdf_page, token_index, text, block_role)``.
    """
    return list(conn.execute(
        """
        SELECT pdf_page, token_index, text, block_role
        FROM tokens
        WHERE block_type = 'body'
          AND (block_role IS NULL OR block_role != 'chapter_title')
          AND (
            (pdf_page > ? OR (pdf_page = ? AND token_index >= ?))
            AND
            (pdf_page < ? OR (pdf_page = ? AND token_index <= ?))
          )
        ORDER BY pdf_page ASC, token_index ASC
        """,
        (
            start_pdf_page, start_pdf_page, start_token_offset,
            end_pdf_page, end_pdf_page, end_token_offset,
        ),
    ).fetchall())


# ---------------------------------------------------------------------------
# chunk_text assembly — H-4 determinism critical
# ---------------------------------------------------------------------------


def _join_tokens(rows: list[tuple[int, int, str, str | None]]) -> str:
    """Join token rows into prose with paragraph-boundary inference.

    Paragraph boundary heuristic (H-4-safe):
    - Any transition across a ``pdf_page`` boundary ⇒ insert ``"\\n\\n"``.
    - Consecutive tokens on the SAME ``pdf_page`` with ``token_index`` gap > 1
      ⇒ insert ``"\\n\\n"`` (a gap signals a block break by Phase 1's writer
      guarantee — tokens are inserted in (pdf_page, token_index) order with
      no gaps within a single block).
    - Else: insert a single ``" "`` between token texts.
    - Token ``text`` is used VERBATIM (surface form — D-16 step 2). No
      normalization, no case folding.

    Returns an LF-only string with no BOM, no CR, no trailing whitespace
    on any line.
    """
    if not rows:
        return ""
    out: list[str] = []
    prev_page: int | None = None
    prev_idx: int | None = None
    for pdf_page, token_index, text, _role in rows:
        if prev_page is None:
            # First token — no separator.
            out.append(text)
        else:
            same_page = pdf_page == prev_page
            gap = token_index - (prev_idx or 0)
            if (not same_page) or gap > 1:
                out.append("\n\n")
                out.append(text)
            else:
                out.append(" ")
                out.append(text)
        prev_page = pdf_page
        prev_idx = token_index
    joined = "".join(out)
    # H-4: strip trailing whitespace on each line; normalize CR just in case.
    lines = [
        ln.rstrip()
        for ln in joined.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    return "\n".join(lines)


def build_chunk_text(conn: sqlite3.Connection, chapter_num: int) -> str:
    """Build the canonical ``chunk_text`` for chapter ``N`` with D-07 overlaps.

    Layout (LF-separated; empty overlap regions collapse):
    ::

        <prior-overlap (last N.NN of chapter N-1)>

        <chapter N body>

        <forward-overlap (first N.NN of chapter N+1)>

    ``ch1`` has no prior-overlap; the last chapter has no forward-overlap
    (detected by absence of a chapter ``N+1`` in the ``sections`` table).
    """
    bounds = _chapter_bounds(conn, chapter_num)
    if bounds is None:
        raise ValueError(f"no chapter={chapter_num} section found in corpus")
    s_pg, s_tk, e_pg, e_tk = bounds

    prior_overlap = _last_major_section_of_chapter(conn, chapter_num - 1)
    next_overlap = _first_major_section_of_chapter(conn, chapter_num + 1)

    segments: list[str] = []
    if prior_overlap is not None:
        prior_text = _join_tokens(_fetch_body_tokens(conn, *prior_overlap))
        if prior_text:
            segments.append(prior_text)

    body_text = _join_tokens(_fetch_body_tokens(conn, s_pg, s_tk, e_pg, e_tk))
    segments.append(body_text)

    if next_overlap is not None:
        next_text = _join_tokens(_fetch_body_tokens(conn, *next_overlap))
        if next_text:
            segments.append(next_text)

    # D-07: segments are separated by a blank-line paragraph break.
    # LF-only; no trailing whitespace per H-4.
    return "\n\n".join(s for s in segments if s)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def build_all_chunks(conn: sqlite3.Connection) -> list[Chunk]:
    """Build every chapter-chunk in the corpus. Returns chunks sorted by chapter.

    Chapter count is derived from ``SELECT DISTINCT chapter FROM sections
    WHERE section_level = 0`` — no hard-coded 5 — so the same function runs
    unchanged on Pretrial and Trial Advocacy (per CLI-04 reusability goal).
    """
    chapters = [
        int(row[0]) for row in conn.execute(
            "SELECT DISTINCT chapter FROM sections "
            "WHERE section_level = 0 ORDER BY chapter ASC"
        ).fetchall()
    ]
    chunks: list[Chunk] = []
    for ch in chapters:
        bounds = _chapter_bounds(conn, ch)
        if bounds is None:
            continue  # defensive — should never happen given the DISTINCT query
        s_pg, _s_tk, e_pg, _e_tk = bounds
        text = build_chunk_text(conn, ch)
        chunks.append(Chunk(
            chunk_id=f"ch{ch}",
            chapter=ch,
            start_pdf_page=s_pg,
            end_pdf_page=e_pg,
            chunk_text=text,
        ))
    return chunks
