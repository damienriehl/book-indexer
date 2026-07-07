"""Tests for src/book_indexer/render/coverage.py — AUD-03.

Covers ≥13 sub-tests per Plan 05-03:
  - All 9 new section headers present (§§7-15)
  - Phase 5 Calibration section replaces Phase 4 Calibration Pointer (Open Q4)
  - Synthesized entries surface in §13 only (Open Q3)
  - Per-chapter / per-section / folio-tier / section-tier histograms
  - Dropped candidates table (B-05 cruft + Phase 4 deconflict + zero-evidence)
  - Orphan variants table
  - Range collapses summary
  - Render performance placeholder
  - LF-only output + determinism
  - Integration smoke against live coverage.draft.md + IR + SQLite
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from book_indexer.render.coverage import (
    PER_SECTION_TOPN,
    extend_coverage_report,
)
from book_indexer.render.synthesize import SyntheticEntry


# -----------------------------------------------------------------------------
# Fixtures — synthetic IR + draft + SQLite + ledger
# -----------------------------------------------------------------------------


PHASE4_DRAFT = """# Coverage Report (DRAFT)

Phase 4 emits this draft; Phase 5 finalizes it.

## 1. Pool Size

- Surviving index entries: **3**
- ASM-07 target band: **800-1500**

## 4. Attrition Funnel

| Stage | Count |
|---|---|
| pre-dedup candidates | 10 |
| post-dedup buckets | 5 |
| post-deconflict (D-04) | 4 |
| post-zero-evidence drop | 3 |

## 5. Notes

- Buckets dropped as table-citations (D-04): **1**

## 6. Calibration Pointer

Phase 5 (Plan 04-05 cold-build acceptance gate) calibrates the
size-band thresholds. Initial estimates from RESEARCH §H-12 are
PRESERVED in the test file's calibration block until Phase 5
edits them per the 15%-headroom policy.
""".encode("utf-8")


@pytest.fixture
def synth_tree(make_entry, make_locator, make_provenance):
    """Synthetic IndexTree-like with 3 entries across 2 chapters."""
    from book_indexer.render import IndexTree

    e1 = make_entry(
        canonical="hearsay rule",
        locators=[make_locator(section_ref="§1.02", folio="78", evidence_id=1)],
        variants=["hearsay-rule"],
    )
    e2 = make_entry(
        canonical="voir dire",
        locators=[
            make_locator(section_ref="§2.04", folio="120", evidence_id=2),
            make_locator(section_ref="§2.04", folio="121", evidence_id=3),
        ],
        variants=["voir-dire"],
    )
    e3 = make_entry(
        canonical="objection",
        locators=[make_locator(section_ref="§1.02", folio="80", evidence_id=4)],
        variants=[],
    )
    return IndexTree(
        schema_version="1.0",
        entries=[e1, e2, e3],
        provenance=make_provenance(),
    )


@pytest.fixture
def synth_ledger():
    return [
        {"id": 1, "canonical_term": "hearsay rule", "matched_variant": "hearsay-rule",
         "section_ref": "§1.02", "folio": "78"},
        {"id": 2, "canonical_term": "voir dire", "matched_variant": "voir dire",
         "section_ref": "§2.04", "folio": "120"},
        {"id": 3, "canonical_term": "voir dire", "matched_variant": "voir dire",
         "section_ref": "§2.04", "folio": "121"},
        {"id": 4, "canonical_term": "objection", "matched_variant": "objection",
         "section_ref": "§1.02", "folio": "80"},
    ]


@pytest.fixture
def synth_sections_payload():
    return {
        "schema_version": "1.0",
        "sections": [
            {"section_ref": "§1.02", "level": 2, "title": "Foo", "chapter": 1,
             "start_pdf_page": 78, "end_pdf_page": 90, "start_folio": "78"},
            {"section_ref": "§2.04", "level": 2, "title": "Bar", "chapter": 2,
             "start_pdf_page": 119, "end_pdf_page": 130, "start_folio": "119"},
        ],
    }


@pytest.fixture
def synth_synthetics(make_locator):
    from book_indexer.render.ir import Locator

    return [
        SyntheticEntry(
            stem="hearsay",
            sibling_canonicals=("hearsay rule", "hearsay statement"),
            locators=(make_locator(section_ref="§1.02", folio="78", evidence_id=1),),
        ),
    ]


@pytest.fixture
def synth_corpus(tmp_path: Path) -> Path:
    """Synthetic SQLite providing folio_resolution_audit only (coverage.py
    consumes this for the folio-tier histogram).
    """
    db = tmp_path / "synth.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE folio_resolution_audit (
            pdf_page INTEGER PRIMARY KEY,
            final_folio TEXT,
            final_tier TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO folio_resolution_audit (pdf_page, final_folio, final_tier) VALUES (?,?,?)",
        [(1, "i", "TIER_2"), (2, "ii", "TIER_2"), (3, None, "NONE")],
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def render_metrics():
    return {
        "markdown_wall_clock_s": None,
        "docx_wall_clock_s": None,
        "audit_wall_clock_s": None,
        "coverage_wall_clock_s": None,
    }


@pytest.fixture
def b05_drops():
    return ['" hearsay', "( fre", "• craft"]


@pytest.fixture
def phase4_provenance():
    return {
        "dropped_table_citations": [
            {"lemma_key": "fre 706", "matched_category": "rules"},
        ],
        "zero_evidence_drops": ["accurate record lawyer", "ai product"],
    }


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_per_section_topn_constant():
    assert PER_SECTION_TOPN == 20


def test_output_starts_with_phase4_draft_content(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    # Phase 4 draft preamble preserved.
    assert "# Coverage Report (DRAFT)" in text
    assert "## 1. Pool Size" in text
    # The Phase 4 draft text up through the Calibration Pointer header is preserved.
    assert "Surviving index entries: **3**" in text


def test_all_nine_new_section_headers_present(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    for header in (
        "## 7. Per-Chapter Concept Counts",
        "## 8. Per-Section Concept Counts",
        "## 9. Folio-Tier Histogram",
        "## 10. Section-Tier Histogram",
        "## 11. Dropped Candidates",
        "## 12. Orphan Variants",
        "## 13. Synthesized Main Entries",
        "## 14. Page-Range Collapses",
        "## 15. Render Performance",
    ):
        assert header in text, f"missing {header}"


def test_phase5_calibration_section_replaces_phase4_pointer(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    """Open Q4: Phase 4 'Calibration Pointer' is REPLACED by Phase 5 calibration."""
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    # Phase 5 Calibration block present.
    assert "## Phase 5 Calibration" in text
    # Phase 4 Calibration Pointer body removed.
    assert "Phase 5 (Plan 04-05 cold-build acceptance gate)" not in text
    # The Phase 4 "## 6. Calibration Pointer" header is gone too.
    assert "## 6. Calibration Pointer" not in text


def test_phase5_calibration_filled_in_at_build_time(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    """Plan 05-05 calibration: deterministic actuals filled at build time.

    Open Q4 (Plan 05-05): the Calibration block's deterministic markers
    (b05_drop_count, b06_synthesize_count, range_collapse_total,
    entries_in_md, evidence_rows_in_audit) are filled in at build time
    from upstream artifacts. They are deterministic-by-construction so
    embedding them keeps Lock #5 byte-identity intact.

    Wall-clock numbers are NOT embedded — those vary across runs, would
    break Lock #5, and live in stdout telemetry / metadata.json instead.
    """
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    # Phase 5 Calibration block: deterministic actuals filled in.
    cal_block = text.split("## Phase 5 Calibration")[1]
    # No <actual> markers remain in the deterministic block.
    cal_only = cal_block.split("##")[0] if "##" in cal_block else cal_block
    assert "<actual>" not in cal_only, (
        "Plan 05-05: deterministic placeholders must be filled at build time"
    )
    # Specific deterministic values are embedded.
    assert f"b05_drop_count: {len(b05_drops)}" in cal_only
    assert f"b06_synthesize_count: {len(synth_synthetics)}" in cal_only
    assert "range_collapse_total: 0" in cal_only
    # Wall-clock is documented as non-deterministic (not a number).
    assert "cold_render_wall_clock_s: n/a" in cal_only

    # Render Performance section uses 'n/a (see telemetry)' not <actual>.
    perf_block = text.split("## 15. Render Performance")[1].split("##")[0]
    assert "<actual>" not in perf_block, (
        "Plan 05-05: render performance markers must say n/a (Lock #5)"
    )
    assert "n/a (see telemetry)" in perf_block


def test_per_chapter_counts_match_ir(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    # Synthetic IR: 2 entries with §1.02 first locator → §1: 2; 1 entry with §2.04 → §2: 1.
    pc_block = text.split("## 7. Per-Chapter Concept Counts")[1].split("## 8.")[0]
    assert "§1" in pc_block
    assert "§2" in pc_block
    assert "| 2 |" in pc_block  # §1 count
    assert "| 1 |" in pc_block  # §2 count


def test_per_section_topn_cap(
    make_entry, make_locator, make_provenance, synth_ledger, synth_sections_payload,
    render_metrics, b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    """Per-Section table caps at PER_SECTION_TOPN=20."""
    from book_indexer.render import IndexTree
    # Build 25 entries, each with a distinct section_ref.
    entries = []
    for i in range(25):
        entries.append(
            make_entry(
                canonical=f"entry-{i:02d}",
                id=f"e-{i:02d}",
                locators=[make_locator(section_ref=f"§{i + 1}.01", folio=str(i),
                                        evidence_id=i + 1)],
            )
        )
    tree = IndexTree(schema_version="1.0", entries=entries, provenance=make_provenance())

    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    ps_block = text.split("## 8. Per-Section Concept Counts")[1].split("## 9.")[0]
    # Count rows that look like `| §X.YY | N |` — should be ≤ PER_SECTION_TOPN.
    rows = [line for line in ps_block.splitlines() if line.startswith("| §")]
    assert len(rows) <= PER_SECTION_TOPN


def test_synthesized_table_one_row_per_synthetic(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    syn_block = text.split("## 13. Synthesized Main Entries")[1].split("## 14.")[0]
    # The single fixture stem 'hearsay' must appear; columns are stem, sibling_count, locator_count.
    assert "hearsay" in syn_block
    assert "stem" in syn_block.lower()


def test_empty_synthetics_emits_header_only_table(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=[],  # empty
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    syn_block = text.split("## 13. Synthesized Main Entries")[1].split("## 14.")[0]
    # Header row present but no data rows.
    assert "stem" in syn_block.lower()
    # Either no data rows OR an explicit "(none)" line.
    data_rows = [line for line in syn_block.splitlines()
                 if line.startswith("|") and "stem" not in line.lower()
                 and not set(line.replace("|", "").strip()).issubset({"-", " ", ":"})]
    assert len(data_rows) == 0 or any("none" in r.lower() or "(0)" in r for r in data_rows)


def test_empty_b05_drops_renders_zero_count_row(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=[],  # empty
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    drop_block = text.split("## 11. Dropped Candidates")[1].split("## 12.")[0]
    # B-05 row should be present with count 0.
    assert "B-05" in drop_block
    assert "| 0 |" in drop_block


def test_range_collapses_summary_uses_total(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=7,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    rc_block = text.split("## 14. Page-Range Collapses")[1].split("## 15.")[0]
    assert "7" in rc_block


def test_output_lf_only(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    assert b"\r" not in out


def test_determinism(
    synth_tree, synth_ledger, synth_sections_payload, render_metrics,
    b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    a = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    b = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=synth_tree,
        evidence_ledger=synth_ledger,
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    assert a == b


def test_orphan_variants_table(
    make_entry, make_locator, make_provenance, synth_ledger, synth_sections_payload,
    render_metrics, b05_drops, synth_synthetics, synth_corpus, phase4_provenance,
):
    """Variants in IR with NO matched_variant in evidence are orphans."""
    from book_indexer.render import IndexTree
    e = make_entry(
        canonical="hearsay rule",
        locators=[make_locator(section_ref="§1.02", folio="78", evidence_id=1)],
        variants=["hearsay-rule", "completely-orphaned-variant-xyz"],
    )
    tree = IndexTree(schema_version="1.0", entries=[e], provenance=make_provenance())
    out = extend_coverage_report(
        draft_md=PHASE4_DRAFT,
        tree=tree,
        evidence_ledger=synth_ledger,  # has matched_variant 'hearsay-rule' only
        sections_payload=synth_sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synth_synthetics,
        range_collapses_total=0,
        corpus_path=synth_corpus,
        phase4_provenance=phase4_provenance,
    )
    text = out.decode("utf-8")
    orphan_block = text.split("## 12. Orphan Variants")[1].split("## 13.")[0]
    assert "completely-orphaned-variant-xyz" in orphan_block


# -----------------------------------------------------------------------------
# Integration smoke against live files.
# -----------------------------------------------------------------------------


LIVE_DRAFT = Path("artifacts/coverage.draft.md")
LIVE_TREE = Path("artifacts/index_tree.json")
LIVE_EVIDENCE = Path("artifacts/index_tree_evidence.json")
LIVE_SQLITE = Path("artifacts/page_corpus.sqlite")
LIVE_PROVENANCE = Path("artifacts/index_tree.provenance.json")


@pytest.mark.skipif(
    not (LIVE_DRAFT.exists() and LIVE_TREE.exists()
         and LIVE_EVIDENCE.exists() and LIVE_SQLITE.exists()
         and LIVE_PROVENANCE.exists()),
    reason="live artifacts not present",
)
def test_extend_coverage_live_smoke():
    import json

    import orjson

    from book_indexer.assembly import IndexTree as _IT
    from book_indexer.render.audit import dump_sections

    draft = LIVE_DRAFT.read_bytes()
    tree = _IT(**json.loads(LIVE_TREE.read_bytes()))
    evidence = json.loads(LIVE_EVIDENCE.read_bytes())["entries"]
    provenance = json.loads(LIVE_PROVENANCE.read_bytes())

    conn = sqlite3.connect(f"file:{LIVE_SQLITE}?mode=ro", uri=True)
    sections_payload = orjson.loads(dump_sections(conn))
    conn.close()

    out = extend_coverage_report(
        draft_md=draft,
        tree=tree,
        evidence_ledger=evidence,
        sections_payload=sections_payload,
        render_metrics={},
        b05_drops=['" hearsay'],
        b06_synthetics=[],
        range_collapses_total=0,
        corpus_path=LIVE_SQLITE,
        phase4_provenance=provenance,
    )
    text = out.decode("utf-8")
    # All 9 new section headers present.
    for header in (
        "## 7. Per-Chapter Concept Counts",
        "## 8. Per-Section Concept Counts",
        "## 9. Folio-Tier Histogram",
        "## 10. Section-Tier Histogram",
        "## 11. Dropped Candidates",
        "## 12. Orphan Variants",
        "## 13. Synthesized Main Entries",
        "## 14. Page-Range Collapses",
        "## 15. Render Performance",
        "## Phase 5 Calibration",
    ):
        assert header in text, f"missing {header}"
    # Output size sanity.
    assert len(out) >= len(draft)
