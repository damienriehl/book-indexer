"""Tests for src/book_indexer/render/audit.py — AUD-01 + AUD-02.

Covers ≥14 sub-tests per Plan 05-03:
  - dump_page_corpus format + LF + token order + page order + no-CR
  - dump_sections schema + sort + orjson sort-key invariance
  - enrich_evidence_ledger join + RenderError + sorted-by-id
  - build_audit_bundle 3-key contract + determinism
  - SQLite read-only mode enforcement
  - Integration smoke against live artifacts/page_corpus.sqlite (skipif)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import orjson
import pytest

from book_indexer.render.audit import (
    build_audit_bundle,
    dump_page_corpus,
    dump_sections,
    enrich_evidence_ledger,
)
from book_indexer.render.errors import RenderError


# -----------------------------------------------------------------------------
# Synthetic SQLite fixture
# -----------------------------------------------------------------------------


@pytest.fixture
def synth_db(tmp_path: Path) -> Path:
    """3-page synthetic SQLite mirroring Phase 1's schema (subset)."""
    db_path = tmp_path / "synth_corpus.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE pages (
            pdf_page INTEGER PRIMARY KEY,
            folio TEXT,
            folio_style TEXT,
            folio_tier TEXT,
            page_section TEXT,
            active_chapter_section TEXT,
            section TEXT NOT NULL DEFAULT '',
            bbox_page TEXT NOT NULL DEFAULT '',
            block_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tokens (
            id INTEGER PRIMARY KEY,
            pdf_page INTEGER NOT NULL,
            token_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            norm TEXT NOT NULL,
            lemma TEXT NOT NULL,
            block_type TEXT NOT NULL,
            block_role TEXT,
            bbox TEXT NOT NULL DEFAULT '',
            font_size REAL NOT NULL DEFAULT 0,
            font_name TEXT NOT NULL DEFAULT '',
            crosses_page_break INTEGER NOT NULL DEFAULT 0,
            section_id INTEGER
        );
        CREATE TABLE sections (
            section_id INTEGER PRIMARY KEY,
            section_ref TEXT NOT NULL,
            global_id TEXT NOT NULL,
            section_level INTEGER NOT NULL,
            chapter INTEGER NOT NULL,
            parent_id INTEGER,
            title TEXT NOT NULL,
            start_pdf_page INTEGER NOT NULL,
            start_token_offset INTEGER NOT NULL,
            end_pdf_page INTEGER NOT NULL,
            end_token_offset INTEGER NOT NULL,
            start_folio TEXT NOT NULL
        );
        CREATE TABLE folio_resolution_audit (
            pdf_page INTEGER PRIMARY KEY,
            tier1_label TEXT,
            tier2_position TEXT,
            tier2_raw_text TEXT,
            tier2_match TEXT,
            tier3_inferred TEXT,
            tier3_reason TEXT,
            tier4_anchor_page INTEGER,
            tier4_offset INTEGER,
            final_folio TEXT,
            final_tier TEXT NOT NULL
        );
        """
    )

    # Pages 1, 2, 3 — page 1 has no footnote tokens; page 2 has both; page 3 only body.
    cur.executemany(
        "INSERT INTO pages (pdf_page, folio, folio_style) VALUES (?,?,?)",
        [(1, "i", "roman"), (2, "ii", "roman"), (3, "1", "arabic")],
    )

    # Tokens — body tokens for all 3 pages; page 2 has 2 footnote tokens.
    rows = [
        # page 1 (no fn)
        (1, 0, "Hello", "hello", "hello", "body", None),
        (1, 1, "world", "world", "world", "body", None),
        # page 2 (body + footnote)
        (2, 0, "Second", "second", "second", "body", None),
        (2, 1, "page", "page", "page", "body", None),
        (2, 2, "fn1", "fn1", "fn1", "footnote", "footnote"),
        (2, 3, "fn2", "fn2", "fn2", "footnote", "footnote"),
        # page 3 (body only — but tokens emitted out of order in the table to test ordering)
        (3, 1, "beta", "beta", "beta", "body", None),
        (3, 0, "alpha", "alpha", "alpha", "body", None),
    ]
    cur.executemany(
        "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, block_type, block_role) VALUES (?,?,?,?,?,?,?)",
        rows,
    )

    # 2 sections.
    cur.executemany(
        "INSERT INTO sections (section_id, section_ref, global_id, section_level, chapter, parent_id, title, start_pdf_page, start_token_offset, end_pdf_page, end_token_offset, start_folio) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "Chapter 1", "ch1", 0, 1, None, "Intro", 1, 0, 3, 0, "i"),
            (2, "§1", "ch1-s1", 1, 1, 1, "First Section", 2, 0, 3, 0, "ii"),
        ],
    )

    # folio_resolution_audit
    cur.executemany(
        "INSERT INTO folio_resolution_audit (pdf_page, final_folio, final_tier) VALUES (?,?,?)",
        [(1, "i", "TIER_2"), (2, "ii", "TIER_2"), (3, "1", "NONE")],
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def synth_ro_conn(synth_db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{synth_db}?mode=ro", uri=True)


# -----------------------------------------------------------------------------
# dump_page_corpus
# -----------------------------------------------------------------------------


def test_dump_page_corpus_starts_with_page_separator(synth_ro_conn):
    out = dump_page_corpus(synth_ro_conn)
    assert out.startswith(b"===== pdf_page=1 folio=i folio_style=roman =====\n")


def test_dump_page_corpus_emits_footnote_marker_per_page(synth_ro_conn):
    out = dump_page_corpus(synth_ro_conn).decode("utf-8")
    # Three pages → three [FOOTNOTE] markers.
    assert out.count("[FOOTNOTE]\n") == 3


def test_dump_page_corpus_pages_in_ascending_order(synth_ro_conn):
    out = dump_page_corpus(synth_ro_conn).decode("utf-8")
    p1 = out.index("pdf_page=1")
    p2 = out.index("pdf_page=2")
    p3 = out.index("pdf_page=3")
    assert p1 < p2 < p3


def test_dump_page_corpus_tokens_in_token_index_order(synth_ro_conn):
    """Page 3 was inserted with token_index=1 first then 0; output must be ascending."""
    out = dump_page_corpus(synth_ro_conn).decode("utf-8")
    # The body line for page 3 should be "alpha beta", not "beta alpha".
    p3_block = out.split("===== pdf_page=3")[1]
    assert "alpha beta" in p3_block
    assert "beta alpha" not in p3_block


def test_dump_page_corpus_no_carriage_return(synth_ro_conn):
    out = dump_page_corpus(synth_ro_conn)
    assert b"\r" not in out


def test_dump_page_corpus_footnote_tokens_appear_after_marker(synth_ro_conn):
    out = dump_page_corpus(synth_ro_conn).decode("utf-8")
    p2_block = out.split("===== pdf_page=2")[1].split("===== pdf_page=3")[0]
    body_idx = p2_block.index("Second page")
    fn_idx = p2_block.index("[FOOTNOTE]")
    assert body_idx < fn_idx
    assert "fn1 fn2" in p2_block[fn_idx:]


def test_dump_page_corpus_empty_footnote_renders_blank_line(synth_ro_conn):
    """Page 1 has no footnote tokens — '[FOOTNOTE]\\n\\n' grep-stability."""
    out = dump_page_corpus(synth_ro_conn).decode("utf-8")
    p1_block = out.split("===== pdf_page=1")[1].split("===== pdf_page=2")[0]
    assert "[FOOTNOTE]\n\n" in p1_block


# -----------------------------------------------------------------------------
# dump_sections
# -----------------------------------------------------------------------------


def test_dump_sections_schema_keys(synth_ro_conn):
    payload = orjson.loads(dump_sections(synth_ro_conn))
    assert payload["schema_version"] == "1.0"
    assert isinstance(payload["sections"], list)
    assert len(payload["sections"]) == 2
    keys = set(payload["sections"][0].keys())
    assert keys == {
        "section_ref",
        "level",
        "title",
        "chapter",
        "start_pdf_page",
        "end_pdf_page",
        "start_folio",
    }


def test_dump_sections_sort_order(synth_ro_conn):
    payload = orjson.loads(dump_sections(synth_ro_conn))
    secs = payload["sections"]
    # Sort key: (start_pdf_page ASC, level ASC, section_id ASC).
    keys = [(s["start_pdf_page"], s["level"]) for s in secs]
    assert keys == sorted(keys)


def test_dump_sections_orjson_sort_invariance(synth_ro_conn):
    """Output bytes are byte-identical to a re-encoded copy via OPT_SORT_KEYS."""
    out = dump_sections(synth_ro_conn)
    re_encoded = orjson.dumps(
        json.loads(out),
        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
    )
    assert out == re_encoded


# -----------------------------------------------------------------------------
# enrich_evidence_ledger
# -----------------------------------------------------------------------------


@pytest.fixture
def synth_ledger(tmp_path: Path) -> Path:
    """A 3-row ledger referencing two sections from the synthetic SQLite."""
    rows = {
        "entries": [
            {
                "id": 2,
                "canonical_term": "world",
                "matched_variant": "world",
                "section_ref": "§1",
                "section_level": 1,
                "section_path": ["§1"],
                "folio": "ii",
                "pdf_page": 2,
                "match_mode": "lemma",
                "verbatim_snippet": "Second page",
                "token_offset": 0,
            },
            {
                "id": 1,
                "canonical_term": "hello",
                "matched_variant": "hello",
                "section_ref": "Chapter 1",
                "section_level": 0,
                "section_path": ["Chapter 1"],
                "folio": "i",
                "pdf_page": 1,
                "match_mode": "lemma",
                "verbatim_snippet": "Hello world",
                "token_offset": 0,
            },
            {
                "id": 3,
                "canonical_term": "alpha",
                "matched_variant": "alpha",
                "section_ref": "§1",
                "section_level": 1,
                "section_path": ["§1"],
                "folio": "1",
                "pdf_page": 3,
                "match_mode": "exact",
                "verbatim_snippet": "alpha beta",
                "token_offset": 0,
            },
        ]
    }
    p = tmp_path / "ledger.json"
    p.write_bytes(orjson.dumps(rows))
    return p


def test_enrich_evidence_adds_section_bounds(synth_ro_conn, synth_ledger):
    sections = orjson.loads(dump_sections(synth_ro_conn))
    out = enrich_evidence_ledger(synth_ledger, sections)
    payload = orjson.loads(out)
    for row in payload["entries"]:
        assert "section_bounds" in row
        sb = row["section_bounds"]
        assert set(sb.keys()) == {"start_pdf_page", "end_pdf_page", "start_folio"}


def test_enrich_evidence_preserves_original_fields(synth_ro_conn, synth_ledger):
    sections = orjson.loads(dump_sections(synth_ro_conn))
    out = enrich_evidence_ledger(synth_ledger, sections)
    payload = orjson.loads(out)
    row = next(r for r in payload["entries"] if r["id"] == 1)
    assert row["canonical_term"] == "hello"
    assert row["matched_variant"] == "hello"
    assert row["section_ref"] == "Chapter 1"
    assert row["folio"] == "i"
    assert row["pdf_page"] == 1


def test_enrich_evidence_sorted_by_id(synth_ro_conn, synth_ledger):
    sections = orjson.loads(dump_sections(synth_ro_conn))
    out = enrich_evidence_ledger(synth_ledger, sections)
    payload = orjson.loads(out)
    ids = [r["id"] for r in payload["entries"]]
    assert ids == sorted(ids)


def test_enrich_evidence_raises_on_missing_section_ref(
    synth_ro_conn, synth_ledger, tmp_path
):
    bad_rows = {
        "entries": [
            {
                "id": 99,
                "canonical_term": "ghost",
                "matched_variant": "ghost",
                "section_ref": "§999",  # not in synth_db
                "section_level": 1,
                "section_path": ["§999"],
                "folio": "x",
                "pdf_page": 99,
                "match_mode": "lemma",
                "verbatim_snippet": "...",
                "token_offset": 0,
            }
        ]
    }
    p = tmp_path / "bad_ledger.json"
    p.write_bytes(orjson.dumps(bad_rows))
    sections = orjson.loads(dump_sections(synth_ro_conn))
    with pytest.raises(RenderError):
        enrich_evidence_ledger(p, sections)


# -----------------------------------------------------------------------------
# build_audit_bundle
# -----------------------------------------------------------------------------


def test_build_audit_bundle_exactly_three_keys(synth_db, synth_ledger):
    bundle = build_audit_bundle(synth_db, synth_ledger)
    assert set(bundle.keys()) == {
        "page_corpus.txt",
        "sections.json",
        "index_evidence.json",
    }


def test_build_audit_bundle_determinism(synth_db, synth_ledger):
    a = build_audit_bundle(synth_db, synth_ledger)
    b = build_audit_bundle(synth_db, synth_ledger)
    for key in a:
        assert a[key] == b[key], f"{key} differs across invocations"


def test_build_audit_bundle_lf_only(synth_db, synth_ledger):
    bundle = build_audit_bundle(synth_db, synth_ledger)
    for key, content in bundle.items():
        assert b"\r" not in content, f"{key} contains CR bytes"


# -----------------------------------------------------------------------------
# SQLite read-only mode enforcement
# -----------------------------------------------------------------------------


def test_audit_opens_sqlite_read_only(synth_db):
    """build_audit_bundle MUST not write to the SQLite — defense-in-depth Lock #1."""
    # Sanity: a connection opened in mode=ro should reject writes.
    conn = sqlite3.connect(f"file:{synth_db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO pages (pdf_page) VALUES (999)")
    conn.close()


def test_audit_module_uses_mode_ro_string():
    """Source-grep guard — the audit module must contain 'mode=ro' literal."""
    src = Path("src/book_indexer/render/audit.py").read_text()
    assert "mode=ro" in src


# -----------------------------------------------------------------------------
# Integration smoke against the live SQLite + ledger.
# -----------------------------------------------------------------------------


LIVE_SQLITE = Path("artifacts/page_corpus.sqlite")
LIVE_LEDGER = Path("artifacts/index_tree_evidence.json")


@pytest.mark.skipif(
    not (LIVE_SQLITE.exists() and LIVE_LEDGER.exists()),
    reason="live artifacts not present",
)
def test_build_audit_bundle_live_sizes_within_ceilings():
    bundle = build_audit_bundle(LIVE_SQLITE, LIVE_LEDGER)
    assert len(bundle["page_corpus.txt"]) <= 600_000, (
        f"page_corpus.txt {len(bundle['page_corpus.txt'])} > 600KB"
    )
    assert len(bundle["sections.json"]) <= 250_000, (
        f"sections.json {len(bundle['sections.json'])} > 250KB"
    )
    assert len(bundle["index_evidence.json"]) <= 2_000_000, (
        f"index_evidence.json {len(bundle['index_evidence.json'])} > 2MB"
    )
    # Sanity: page_corpus.txt has the right shape.
    pc = bundle["page_corpus.txt"]
    assert pc.startswith(b"===== pdf_page=")
    assert b"[FOOTNOTE]\n" in pc
    assert b"\r" not in pc
