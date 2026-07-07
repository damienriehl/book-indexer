"""AUD-01 + AUD-02 — audit-bundle producers.

Three pure functions emit the audit-side substrate that makes the source book a
defensible publication:

  * ``dump_page_corpus(conn) -> bytes`` (AUD-02) — re-verifiable plaintext
    page-indexed dump from Phase 1's SQLite. Every PDF page emits a
    separator line followed by space-joined body tokens, a ``[FOOTNOTE]``
    marker, and footnote tokens (empty line if none).

  * ``dump_sections(conn) -> bytes`` (AUD-02) — section tree enumeration
    sorted by ``(start_pdf_page ASC, level ASC, section_id ASC)``;
    orjson ``OPT_SORT_KEYS | OPT_INDENT_2``.

  * ``enrich_evidence_ledger(ledger_path, sections_payload) -> bytes``
    (AUD-01) — reads Phase 4's existing 2186-row evidence ledger and
    enriches each row with ``section_bounds`` (start/end pdf_page +
    start_folio) joined from the sections payload.

  * ``build_audit_bundle(corpus_path, ledger_path) -> dict[str, bytes]``
    composes the three into the audit bundle. Wave 3's
    ``__main__.py build`` atomically writes them to
    ``artifacts/audit/{page_corpus.txt, sections.json, index_evidence.json}``.

Critical design contracts (RESEARCH §H-7, §H-8, §H-12):
  - SQLite is opened with ``mode=ro`` URI flag (defense-in-depth — Lock
    #1: Phase 5 NEVER writes to the corpus).
  - All queries use ``ORDER BY`` for byte-determinism.
  - All emitted bytes are LF-only (no CRLF).
  - Both JSON outputs use orjson ``OPT_SORT_KEYS | OPT_INDENT_2``.
  - ``enrich_evidence_ledger`` raises ``RenderError`` on any unmatched
    section_ref (Phase 4 invariant should make this impossible; defensive
    against drift).

Per Open Question 3 (RESEARCH §H-12): synthesized entries (B-06) are
render-time projections — they appear ONLY in coverage.md section 13,
never in ``index_evidence.json``. This module emits per-Locator data only.

requirements_addressed: AUD-01, AUD-02.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import orjson

from .errors import RenderError

__all__ = [
    "build_audit_bundle",
    "dump_page_corpus",
    "dump_sections",
    "enrich_evidence_ledger",
]


_ORJSON_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2


# -----------------------------------------------------------------------------
# AUD-02 — page_corpus.txt
# -----------------------------------------------------------------------------


def dump_page_corpus(conn: sqlite3.Connection) -> bytes:
    """Emit the page-indexed plaintext dump (AUD-02).

    Format per CONTEXT 'Specifics' + RESEARCH §H-8:

        ===== pdf_page=N folio=F folio_style=S =====
        body tokens space-joined in token_index ASC
        [FOOTNOTE]
        footnote tokens space-joined in token_index ASC

    Pages with no footnote tokens still emit ``[FOOTNOTE]\\n\\n``
    (grep-stability per RESEARCH §H-8). NULL folios (front matter) emit
    as empty strings. All bytes are LF-only.
    """
    cur = conn.cursor()

    pages: list[tuple[int, str, str]] = []
    for row in cur.execute(
        "SELECT pdf_page, folio, folio_style FROM pages ORDER BY pdf_page ASC"
    ):
        pdf_page, folio, folio_style = row
        pages.append((pdf_page, folio or "", folio_style or ""))

    # Collect tokens per page in a single sweep, partitioned by block_type.
    body_tokens: dict[int, list[str]] = {p[0]: [] for p in pages}
    foot_tokens: dict[int, list[str]] = {p[0]: [] for p in pages}
    for row in cur.execute(
        "SELECT pdf_page, token_index, text, block_type "
        "FROM tokens ORDER BY pdf_page ASC, token_index ASC"
    ):
        pdf_page, _idx, text, block_type = row
        if pdf_page not in body_tokens:
            # Defensive: token references a page absent from `pages`. Add
            # late so we don't lose audit data, though Phase 1 invariants
            # forbid this.
            body_tokens[pdf_page] = []
            foot_tokens[pdf_page] = []
        if block_type == "footnote":
            foot_tokens[pdf_page].append(text)
        else:
            body_tokens[pdf_page].append(text)

    parts: list[str] = []
    for pdf_page, folio, folio_style in pages:
        parts.append(
            f"===== pdf_page={pdf_page} folio={folio} folio_style={folio_style} =====\n"
        )
        body = " ".join(body_tokens.get(pdf_page, []))
        parts.append(body + "\n")
        parts.append("[FOOTNOTE]\n")
        foot = " ".join(foot_tokens.get(pdf_page, []))
        parts.append(foot + "\n")

    return "".join(parts).encode("utf-8")


# -----------------------------------------------------------------------------
# AUD-02 — sections.json
# -----------------------------------------------------------------------------


def dump_sections(conn: sqlite3.Connection) -> bytes:
    """Emit the section tree enumeration (AUD-02).

    Output schema:

        {
          "schema_version": "1.0",
          "sections": [
            {
              "section_ref": "Chapter 1",
              "level": 0,
              "title": "Planning to Win: ...",
              "chapter": 1,
              "start_pdf_page": 42,
              "end_pdf_page": 96,
              "start_folio": "1"
            },
            ...
          ]
        }

    Sort: ``(start_pdf_page ASC, level ASC, section_id ASC)`` per RESEARCH §H-8.
    """
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT section_ref, section_level, title, chapter,
               start_pdf_page, end_pdf_page, start_folio, section_id
          FROM sections
         ORDER BY start_pdf_page ASC, section_level ASC, section_id ASC
        """
    ).fetchall()

    sections: list[dict[str, Any]] = []
    for (
        section_ref,
        section_level,
        title,
        chapter,
        start_pdf_page,
        end_pdf_page,
        start_folio,
        _section_id,
    ) in rows:
        sections.append(
            {
                "section_ref": section_ref,
                "level": section_level,
                "title": title,
                "chapter": chapter,
                "start_pdf_page": start_pdf_page,
                "end_pdf_page": end_pdf_page,
                "start_folio": start_folio,
            }
        )

    payload = {"schema_version": "1.0", "sections": sections}
    return orjson.dumps(payload, option=_ORJSON_OPTS)


# -----------------------------------------------------------------------------
# AUD-01 — enrich evidence ledger
# -----------------------------------------------------------------------------


def enrich_evidence_ledger(ledger_path: Path, sections_payload: dict) -> bytes:
    """Enrich Phase 4's evidence ledger with section bounds (AUD-01).

    Reads ``ledger_path`` (which has shape ``{"entries": [{...}, ...]}``);
    for each row, looks up the section_ref in ``sections_payload`` and
    appends ``section_bounds: {start_pdf_page, end_pdf_page, start_folio}``.

    Raises:
        RenderError: if any row's section_ref is not in the sections
            payload — defense in depth against Phase 4 / Phase 1 schema
            drift.

    Output rows are sorted by ``id`` ASC (monotonic from Phase 4) for
    byte-determinism. orjson ``OPT_SORT_KEYS | OPT_INDENT_2``.
    """
    ledger = orjson.loads(Path(ledger_path).read_bytes())
    section_index: dict[str, dict[str, Any]] = {
        s["section_ref"]: s for s in sections_payload.get("sections", [])
    }

    enriched: list[dict[str, Any]] = []
    for row in ledger.get("entries", []):
        section_ref = row.get("section_ref")
        sec = section_index.get(section_ref)
        if sec is None:
            raise RenderError(
                f"section_ref={section_ref!r} not found in sections payload "
                f"(row id={row.get('id')!r}). Phase 4/1 schema drift?"
            )
        enriched_row = dict(row)
        enriched_row["section_bounds"] = {
            "start_pdf_page": sec["start_pdf_page"],
            "end_pdf_page": sec["end_pdf_page"],
            "start_folio": sec["start_folio"],
        }
        enriched.append(enriched_row)

    enriched.sort(key=lambda r: r["id"])
    payload = {"schema_version": "1.0", "entries": enriched}
    return orjson.dumps(payload, option=_ORJSON_OPTS)


# -----------------------------------------------------------------------------
# Composition
# -----------------------------------------------------------------------------


def build_audit_bundle(corpus_path: Path, ledger_path: Path) -> dict[str, bytes]:
    """Compose the 3-file audit bundle.

    Opens ``corpus_path`` in SQLite ``mode=ro`` (defense-in-depth — Lock
    #1) and reads ``ledger_path`` as JSON. Returns a dict keyed by
    output filename — caller atomically writes each value to
    ``artifacts/audit/<key>``.

    The output is byte-deterministic: 2 invocations on identical inputs
    return identical bytes.
    """
    uri = f"file:{Path(corpus_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        page_corpus = dump_page_corpus(conn)
        sections_bytes = dump_sections(conn)
    finally:
        conn.close()

    sections_payload = orjson.loads(sections_bytes)
    evidence_bytes = enrich_evidence_ledger(ledger_path, sections_payload)

    return {
        "page_corpus.txt": page_corpus,
        "sections.json": sections_bytes,
        "index_evidence.json": evidence_bytes,
    }
