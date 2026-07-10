"""Deterministic SQLite / FTS5 corpus writer (D-19, D-20, SEC-06).

This module is the PHASE-1 OUTPUT MACHINE — every path through the ingest
pipeline terminates by writing a ``page_corpus.sqlite`` file on disk. The
determinism contract (QUAL-01) requires two invocations with the same PDF
and the same spaCy model to produce byte-identical files, which means
*every* write must be order-stable and every non-content field (timestamps,
rowids) must be reproducible.

Pragmas (applied BEFORE DDL):
  - ``journal_mode = DELETE`` — WAL writes a sidecar shm file whose contents
    vary across runs; DELETE mode keeps journal bytes purely transactional.
  - ``synchronous = FULL`` — fsync on every commit; acceptable for Phase 1's
    write-once-read-many model, required for crash-safe rebuilds.
  - ``page_size = 4096`` — pinned so the file layout is identical across
    platforms with different filesystem page sizes.
  - ``auto_vacuum = NONE`` — auto_vacuum sprinkles free-page pointers that
    differ between runs depending on insert order.
  - ``locking_mode = EXCLUSIVE`` — no concurrent readers mid-build.
  - ``foreign_keys = ON`` — enforce ``tokens.pdf_page`` / ``tokens.section_id``
    referential integrity at write time so broken joins surface immediately.

Schema highlights (merges the plan's base DDL with the Section-Primary Pivot
additions from CONTEXT §D-19 extension + `<additional_context>`):

  - ``pages`` — one row per PDF page.
  - ``sections`` — one row per detected heading; 4-level CHECK accepts
    ``{0, 1, 2, 3}`` (Chapter → § N → N.NN → N.NN.M).
  - ``tokens`` — per-token row with ``section_id`` FK to the DEEPEST
    containing section (NULL for footnote / header_footer / image tokens
    and for body tokens that fall outside every detected section —
    e.g., pre-§-1 front-matter prose).
  - ``tokens_fts`` — FTS5 virtual table with positional postings; sync
    triggers keep it aligned with ``tokens`` on INSERT/UPDATE/DELETE.
  - Audit tables — ``folio_resolution_audit``, ``header_footer_audit``,
    ``classification_audit`` for forensic replay (Phase 5's coverage gate).
  - ``extraction_metadata`` — singleton row with SHAs, version pins, and
    ``build_frozen_ts = '1970-01-01T00:00:00Z'``.

Section-ID assignment (D-24) is handled by :func:`assign_section_ids`, which
walks every body token and picks the deepest section whose closed interval
``[(start_pdf_page, start_token_offset), (end_pdf_page, end_token_offset)]``
contains the token's ``(pdf_page, token_index)``. Footnote / header / image
tokens keep ``section_id = NULL`` by construction.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import orjson

from .folio_resolver import FolioInfo, ResolutionAudit
from .section_resolver import Section
from .tokenizer import TokenRecord
from .types import BlockClassification, StrippedBlock, YBands

FROZEN_TS = "1970-01-01T00:00:00Z"

# ------------------------------------------------------------------
# Schema DDL
# ------------------------------------------------------------------

_DDL = """
CREATE TABLE pages (
    pdf_page                INTEGER PRIMARY KEY,
    folio                   TEXT,
    folio_style             TEXT,
    folio_tier              TEXT,
    page_section            TEXT,
    active_chapter_section  TEXT,
    section                 TEXT NOT NULL,
    bbox_page               TEXT NOT NULL,
    block_count             INTEGER NOT NULL
);

CREATE TABLE sections (
    section_id              INTEGER PRIMARY KEY,
    section_ref             TEXT NOT NULL,
    global_id               TEXT NOT NULL UNIQUE,
    section_level           INTEGER NOT NULL CHECK(section_level IN (0, 1, 2, 3)),
    chapter                 INTEGER NOT NULL,
    parent_id               INTEGER REFERENCES sections(section_id),
    title                   TEXT NOT NULL,
    start_pdf_page          INTEGER NOT NULL,
    start_token_offset      INTEGER NOT NULL,
    end_pdf_page            INTEGER NOT NULL,
    end_token_offset        INTEGER NOT NULL,
    start_folio             TEXT NOT NULL,
    UNIQUE(start_pdf_page, start_token_offset, section_level)
);

CREATE INDEX idx_sections_parent ON sections(parent_id);
CREATE INDEX idx_sections_start  ON sections(start_pdf_page, start_token_offset);
CREATE INDEX idx_sections_end    ON sections(end_pdf_page, end_token_offset);
CREATE INDEX idx_sections_level  ON sections(section_level);

CREATE TABLE tokens (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_page            INTEGER NOT NULL,
    token_index         INTEGER NOT NULL,
    text                TEXT NOT NULL,
    norm                TEXT NOT NULL,
    lemma               TEXT NOT NULL,
    block_type          TEXT NOT NULL CHECK(block_type IN ('body', 'footnote', 'header_footer')),
    block_role          TEXT,
    bbox                TEXT NOT NULL,
    font_size           REAL NOT NULL,
    font_name           TEXT NOT NULL,
    crosses_page_break  INTEGER NOT NULL DEFAULT 0,
    section_id          INTEGER REFERENCES sections(section_id),
    UNIQUE (pdf_page, token_index),
    FOREIGN KEY (pdf_page) REFERENCES pages(pdf_page)
);

CREATE INDEX idx_tokens_norm     ON tokens(norm);
CREATE INDEX idx_tokens_lemma    ON tokens(lemma);
CREATE INDEX idx_tokens_btype    ON tokens(block_type);
CREATE INDEX idx_tokens_pagepos  ON tokens(pdf_page, token_index);
CREATE INDEX idx_tokens_section  ON tokens(section_id);

CREATE VIRTUAL TABLE tokens_fts USING fts5(
    norm,
    lemma,
    block_type UNINDEXED,
    content='tokens',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER tokens_ai AFTER INSERT ON tokens BEGIN
    INSERT INTO tokens_fts(rowid, norm, lemma, block_type)
    VALUES (new.id, new.norm, new.lemma, new.block_type);
END;

CREATE TRIGGER tokens_ad AFTER DELETE ON tokens BEGIN
    INSERT INTO tokens_fts(tokens_fts, rowid, norm, lemma, block_type)
    VALUES ('delete', old.id, old.norm, old.lemma, old.block_type);
END;

CREATE TRIGGER tokens_au AFTER UPDATE ON tokens BEGIN
    INSERT INTO tokens_fts(tokens_fts, rowid, norm, lemma, block_type)
    VALUES ('delete', old.id, old.norm, old.lemma, old.block_type);
    INSERT INTO tokens_fts(rowid, norm, lemma, block_type)
    VALUES (new.id, new.norm, new.lemma, new.block_type);
END;

CREATE TABLE extraction_metadata (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    pdf_sha256              TEXT NOT NULL,
    pdf_page_count          INTEGER NOT NULL,
    pymupdf_version         TEXT NOT NULL,
    pymupdf_textflags       INTEGER NOT NULL,
    spacy_version           TEXT NOT NULL,
    spacy_model             TEXT NOT NULL,
    spacy_model_sha256      TEXT NOT NULL,
    y_bands                 TEXT NOT NULL,
    body_font_mode          REAL NOT NULL,
    footnote_threshold      REAL NOT NULL,
    section_fixture_sha256  TEXT NOT NULL,
    folio_fixture_sha256    TEXT NOT NULL,
    pipeline_version        TEXT NOT NULL,
    build_frozen_ts         TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'
);

CREATE TABLE folio_resolution_audit (
    pdf_page            INTEGER PRIMARY KEY,
    tier1_label         TEXT,
    tier2_position      TEXT,
    tier2_raw_text      TEXT,
    tier2_match         TEXT,
    tier3_inferred      TEXT,
    tier3_reason        TEXT,
    tier4_anchor_page   INTEGER,
    tier4_offset        INTEGER,
    final_folio         TEXT,
    final_tier          TEXT NOT NULL,
    FOREIGN KEY (pdf_page) REFERENCES pages(pdf_page)
);

CREATE TABLE header_footer_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_page    INTEGER NOT NULL,
    band        TEXT NOT NULL,
    position    TEXT NOT NULL,
    text        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    bbox        TEXT NOT NULL,
    FOREIGN KEY (pdf_page) REFERENCES pages(pdf_page)
);

CREATE TABLE classification_audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_page          INTEGER NOT NULL,
    block_index       INTEGER NOT NULL,
    avg_font_size     REAL NOT NULL,
    body_font_mode    REAL NOT NULL,
    y_center          REAL NOT NULL,
    threshold_y       REAL NOT NULL,
    final_type        TEXT NOT NULL,
    ambiguity_reason  TEXT NOT NULL,
    FOREIGN KEY (pdf_page) REFERENCES pages(pdf_page)
);
"""

PIPELINE_VERSION = "0.1.0"


# ------------------------------------------------------------------
# JSON helper — single source of deterministic JSON in this module
# ------------------------------------------------------------------


def _json(obj) -> str:
    """``orjson`` with sorted keys; returns a str (ASCII-safe since we avoid
    Unicode escapes in the corpus — text is stored verbatim in its own column)."""
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode("utf-8")


# ------------------------------------------------------------------
# Connection + schema
# ------------------------------------------------------------------


def open_deterministic(path: Path) -> sqlite3.Connection:
    """Open (or overwrite) a SQLite connection with all determinism pragmas."""
    path = Path(path)
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    # Pragmas MUST precede any DDL — some (page_size, encoding, auto_vacuum)
    # only take effect on an empty database.
    for pragma in (
        "PRAGMA encoding = 'UTF-8'",
        "PRAGMA page_size = 4096",
        "PRAGMA auto_vacuum = NONE",
        "PRAGMA journal_mode = DELETE",
        "PRAGMA synchronous = FULL",
        "PRAGMA temp_store = MEMORY",
        "PRAGMA locking_mode = EXCLUSIVE",
        "PRAGMA foreign_keys = ON",
    ):
        conn.execute(pragma)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table / index / trigger in one atomic script call."""
    conn.executescript(_DDL)


# ------------------------------------------------------------------
# pages
# ------------------------------------------------------------------


def _section_for(folio_style: str | None, pdf_page: int) -> str:
    """Coarse document-region label ("front_matter" / "body" / "back_matter")
    used by the ``pages.section`` column. Consumers in Phase 3 scope queries
    by this tag.
    """
    if folio_style == "roman":
        return "front_matter"
    if folio_style == "arabic":
        return "body"
    if folio_style == "prefix":
        return "back_matter"
    if pdf_page < 40:
        return "front_matter"
    return "body"


def write_pages(
    conn: sqlite3.Connection,
    folio_infos: dict[int, FolioInfo],
    page_sections: dict[int, str | None],
    active_chapter_sections: dict[int, str | None],
    block_counts: dict[int, int],
    bbox_page_per_page: dict[int, tuple[float, float, float, float]],
) -> None:
    """Insert one row per pdf_page (ascending). All inputs are keyed by
    ``pdf_page``; missing entries default to sensible zero-ish values."""
    rows = []
    for pdf_page in sorted(folio_infos.keys()):
        info = folio_infos[pdf_page]
        rows.append((
            pdf_page,
            info.folio,
            info.folio_style,
            info.folio_tier,
            page_sections.get(pdf_page),
            active_chapter_sections.get(pdf_page),
            _section_for(info.folio_style, pdf_page),
            _json(list(bbox_page_per_page.get(pdf_page, (0.0, 0.0, 540.0, 720.0)))),
            int(block_counts.get(pdf_page, 0)),
        ))
    conn.executemany(
        "INSERT INTO pages "
        "(pdf_page, folio, folio_style, folio_tier, page_section, "
        " active_chapter_section, section, bbox_page, block_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# ------------------------------------------------------------------
# sections
# ------------------------------------------------------------------


def write_sections(
    conn: sqlite3.Connection,
    sections: Sequence[Section],
    start_folios: dict[str, str] | None = None,
) -> dict[str, int]:
    """Insert every resolved :class:`Section` and return a
    ``{global_id: section_id}`` map.

    ``start_folios`` maps ``global_id -> printed folio string`` (typically
    looked up by the pipeline from ``pages.folio``). When absent or missing,
    the empty string is stored — consumers treat "" as "no printed folio
    on start page" (blank versos, front-matter pre-roman-i pages, etc.).

    Parent edges are resolved in a second pass: we insert every section with
    ``parent_id=NULL`` first, then UPDATE ``parent_id`` from the
    ``parent_global_id`` of each :class:`Section`. This avoids forward-ref
    issues when a parent appears later in the input list (the tree builder
    guarantees topological order, but we are defensive).
    """
    # Preserve input order so section_id assignment is deterministic.
    ordered = list(sections)
    id_by_gid: dict[str, int] = {}
    rows_pass1 = []
    for i, s in enumerate(ordered, start=1):
        id_by_gid[s.global_id] = i
        start_folio = ""
        if start_folios is not None:
            start_folio = start_folios.get(s.global_id, "") or ""
        rows_pass1.append((
            i,
            s.section_ref,
            s.global_id,
            int(s.section_level),
            int(s.chapter),
            None,                       # parent_id; filled in pass 2
            s.title,
            int(s.start_pdf_page),
            int(s.start_token_offset),
            int(s.end_pdf_page),
            int(s.end_token_offset),
            start_folio,
        ))
    conn.executemany(
        "INSERT INTO sections "
        "(section_id, section_ref, global_id, section_level, chapter, parent_id, "
        " title, start_pdf_page, start_token_offset, end_pdf_page, end_token_offset, start_folio) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows_pass1,
    )

    # Pass 2 — resolve parent_id from parent_global_id.
    for s in ordered:
        if s.parent_global_id is None:
            continue
        pid = id_by_gid.get(s.parent_global_id)
        if pid is None:
            raise ValueError(
                f"Section {s.global_id!r} parent {s.parent_global_id!r} "
                f"not in the sections input (corpus writer)"
            )
        conn.execute(
            "UPDATE sections SET parent_id = ? WHERE global_id = ?",
            (pid, s.global_id),
        )

    return id_by_gid


# ------------------------------------------------------------------
# tokens
# ------------------------------------------------------------------


def write_tokens(conn: sqlite3.Connection, tokens: Iterable[TokenRecord]) -> None:
    """Insert tokens in ``(pdf_page ASC, token_index ASC)`` order — this is
    what gives the ``id AUTOINCREMENT`` a deterministic ROWID assignment
    across runs (T-01-04-05 mitigation).
    """
    rows = []
    for t in sorted(tokens, key=lambda r: (r.pdf_page, r.token_index)):
        rows.append((
            t.pdf_page, t.token_index, t.text, t.norm, t.lemma,
            t.block_type, t.block_role,
            _json(list(t.bbox)),
            float(t.font_size), t.font_name,
            int(t.crosses_page_break),
            None,  # section_id — assigned post-insert by assign_section_ids
        ))
    conn.executemany(
        "INSERT INTO tokens "
        "(pdf_page, token_index, text, norm, lemma, block_type, block_role, "
        " bbox, font_size, font_name, crosses_page_break, section_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def assign_section_ids(conn: sqlite3.Connection) -> None:
    """Assign ``tokens.section_id`` to the DEEPEST section containing each
    body token (D-24 + SEC-06).

    Algorithm: walk levels 3 → 2 → 1 → 0 and for each level, UPDATE every
    body token whose ``(pdf_page, token_index)`` falls in a section's closed
    interval and whose ``section_id`` is still NULL. Higher-deepest sections
    win because they run first; level-0 sweeps last as a fallback for body
    tokens that fall outside any § N / N.NN / N.NN.M range (e.g., chapter
    prologue prose before the first § N heading).

    Footnote / header_footer tokens keep ``section_id = NULL`` by the
    ``WHERE tokens.block_type = 'body'`` filter.
    """
    # Comparison is lexicographic over (pdf_page, token_index): we serialize
    # as integers with SQLite's normal ORDER semantics. Closed-closed
    # interval: start <= pos <= end.
    for level in (3, 2, 1, 0):
        conn.execute(
            """
            UPDATE tokens
            SET section_id = (
                SELECT s.section_id
                FROM sections AS s
                WHERE s.section_level = ?
                  AND (
                    s.start_pdf_page < tokens.pdf_page
                    OR (s.start_pdf_page = tokens.pdf_page
                        AND s.start_token_offset <= tokens.token_index)
                  )
                  AND (
                    s.end_pdf_page > tokens.pdf_page
                    OR (s.end_pdf_page = tokens.pdf_page
                        AND s.end_token_offset >= tokens.token_index)
                  )
                ORDER BY s.start_pdf_page DESC, s.start_token_offset DESC
                LIMIT 1
            )
            WHERE tokens.block_type = 'body'
              AND tokens.section_id IS NULL
            """,
            (level,),
        )


# ------------------------------------------------------------------
# Audit tables
# ------------------------------------------------------------------


def write_folio_audit(conn: sqlite3.Connection, audit: Sequence[ResolutionAudit]) -> None:
    rows = [(
        a.pdf_page, a.tier1_label, a.tier2_position, a.tier2_raw_text, a.tier2_match,
        a.tier3_inferred, a.tier3_reason, a.tier4_anchor_page, a.tier4_offset,
        a.final_folio, a.final_tier,
    ) for a in sorted(audit, key=lambda a: a.pdf_page)]
    conn.executemany(
        "INSERT INTO folio_resolution_audit "
        "(pdf_page, tier1_label, tier2_position, tier2_raw_text, tier2_match, "
        " tier3_inferred, tier3_reason, tier4_anchor_page, tier4_offset, final_folio, final_tier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def write_header_footer_audit(
    conn: sqlite3.Connection, audit: Sequence[StrippedBlock]
) -> None:
    rows = [(
        sb.pdf_page, sb.band, sb.position, sb.text, sb.reason, _json(list(sb.bbox)),
    ) for sb in sorted(audit, key=lambda s: (s.pdf_page, s.bbox[1], s.bbox[0]))]
    conn.executemany(
        "INSERT INTO header_footer_audit (pdf_page, band, position, text, reason, bbox) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def write_classification_audit(
    conn: sqlite3.Connection,
    classifications: Sequence[BlockClassification],
    body_font_mode: float,
    body_top: float,
    body_bot: float,
) -> None:
    """Only ambiguous classifications go to this table (D-11)."""
    threshold_y = body_top + 0.75 * (body_bot - body_top)
    rows = [(
        c.pdf_page, c.block_index,
        float(c.avg_font_size) if c.avg_font_size is not None else 0.0,
        float(body_font_mode),
        float(c.y_center) if c.y_center is not None else 0.0,
        float(threshold_y),
        c.block_type, c.ambiguity_reason,
    ) for c in classifications if c.ambiguity_reason]
    rows.sort(key=lambda r: (r[0], r[1]))
    conn.executemany(
        "INSERT INTO classification_audit "
        "(pdf_page, block_index, avg_font_size, body_font_mode, y_center, "
        " threshold_y, final_type, ambiguity_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# ------------------------------------------------------------------
# extraction_metadata
# ------------------------------------------------------------------


def _sha256_file(path: Path | None) -> str:
    """SHA-256 of a file's bytes, or empty string if path is None/missing."""
    if path is None:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


@dataclass(frozen=True)
class MetadataInputs:
    """Container for ``extraction_metadata`` row fields.

    Keeps the :func:`write_metadata` signature short and self-documenting.
    """
    pdf_path: Path
    pdf_page_count: int
    pymupdf_version: str
    pymupdf_textflags: int
    spacy_version: str
    spacy_model: str
    spacy_model_sha256: str
    y_bands: YBands
    body_font_mode: float
    footnote_threshold: float
    section_fixture_path: Path | None = None
    folio_fixture_path: Path | None = None
    pipeline_version: str = PIPELINE_VERSION


def write_metadata(conn: sqlite3.Connection, meta: MetadataInputs) -> None:
    """Insert the singleton ``extraction_metadata`` row. ``build_frozen_ts``
    is forced to the epoch string (1970-01-01T00:00:00Z) to satisfy QUAL-01's
    byte-identical requirement across runs."""
    pdf_sha = hashlib.sha256(Path(meta.pdf_path).read_bytes()).hexdigest()
    y_bands_json = _json({
        "top_cutoff": meta.y_bands.top_cutoff,
        "bot_cutoff": meta.y_bands.bot_cutoff,
        "body_top": meta.y_bands.body_top,
        "body_bot": meta.y_bands.body_bot,
        "detection_mode": meta.y_bands.detection_mode,
        "top_peak": meta.y_bands.top_peak,
        "bot_peak": meta.y_bands.bot_peak,
        "warning": meta.y_bands.warning,
    })
    conn.execute(
        "INSERT INTO extraction_metadata "
        "(id, pdf_sha256, pdf_page_count, pymupdf_version, pymupdf_textflags, "
        " spacy_version, spacy_model, spacy_model_sha256, y_bands, body_font_mode, "
        " footnote_threshold, section_fixture_sha256, folio_fixture_sha256, "
        " pipeline_version, build_frozen_ts) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pdf_sha,
            int(meta.pdf_page_count),
            meta.pymupdf_version,
            int(meta.pymupdf_textflags),
            meta.spacy_version,
            meta.spacy_model,
            meta.spacy_model_sha256,
            y_bands_json,
            float(meta.body_font_mode),
            float(meta.footnote_threshold),
            _sha256_file(meta.section_fixture_path),
            _sha256_file(meta.folio_fixture_path),
            meta.pipeline_version,
            FROZEN_TS,
        ),
    )


# ------------------------------------------------------------------
# Finalize
# ------------------------------------------------------------------


def finalize(conn: sqlite3.Connection) -> None:
    """VACUUM + ANALYZE then commit + close.

    ``VACUUM`` must run OUTSIDE a transaction. We opened with
    ``isolation_level=None`` so every executed statement is auto-committed,
    which lets us call VACUUM freely.
    """
    conn.execute("ANALYZE")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
