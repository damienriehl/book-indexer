"""End-to-end verify() against the public sample PDF (PKG-06).

This retargets the project's sacred invariant — ``verify()`` is the sole
locator-emitting path, and it works end-to-end against a real ingested corpus
— onto the public-domain ``samples/synthetic_treatise.pdf`` instead of the
private source treatise.

Pipeline exercised, all against real data extracted from the public PDF:

  run_ingest(pdf)  →  page_corpus.sqlite  →  verify(term, conn)  →  Evidence

The folio ("1") asserted below is resolved by the real folio-resolution stage
of ``run_ingest`` from the public PDF — it is NOT hand-authored.

Why the section skeleton is overlaid
-------------------------------------
``section_detector`` is hard-gated on the ``CenturySchoolbook-Bold`` heading
fonts used by the private source treatise. The public sample is typeset in a
generic sans font, so ingest detects zero sections (exactly as documented in
``tests/unit/test_pipeline_sample_pdf.py`` in the source repo). Because
``verify()`` only emits Evidence for tokens that resolve to a section path, we
overlay a minimal, clearly-synthetic 2-level section skeleton onto the REAL
page-4 tokens (which carry the real folio "1"). Everything else — token
extraction, normalization, folio resolution, the matcher, the snippet builder,
and Evidence construction/validation — runs against genuine ingested data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from book_indexer.ingest.pipeline import run_ingest
from book_indexer.verify import Evidence, verify


def _overlay_section_skeleton(conn: sqlite3.Connection) -> None:
    """Attach a minimal §1 → §1.01 hierarchy to the real page-4 tokens.

    The public sample lacks the CenturySchoolbook heading fonts the detector
    requires, so ingest yields no sections. We overlay a 2-level skeleton so
    the full verify() path (matcher + section_path walk + snippet + Evidence)
    can be exercised against otherwise-real ingested rows.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        INSERT INTO sections (section_id, section_ref, global_id, section_level,
          chapter, parent_id, title, start_pdf_page, start_token_offset,
          end_pdf_page, end_token_offset, start_folio)
        VALUES
         (1, '§1',    'gid-1', 1, 1, NULL, 'Advocacy',            4, 0, 4, 999, '1'),
         (2, '§1.01', 'gid-2', 2, 1, 1,    'Why Be an Advocate?', 4, 0, 4, 999, '1');
        """
    )
    conn.execute("UPDATE tokens SET section_id = 2 WHERE pdf_page = 4")
    conn.commit()


def test_verify_end_to_end_on_public_sample(
    tmp_path: Path, sample_pdf_path: Path
) -> None:
    """Ingest the public sample, then prove verify() emits a folio + section path.

    ``procedure`` genuinely appears on page 4 of the sample
    ("Body paragraph about procedure."), which the real folio resolver maps to
    printed folio "1".
    """
    corpus = run_ingest(sample_pdf_path, tmp_path)
    assert corpus.is_file()

    conn = sqlite3.connect(str(corpus))
    try:
        # Sanity: the term is present as a real ingested token before overlay.
        (present,) = conn.execute(
            "SELECT COUNT(*) FROM tokens WHERE norm = 'procedure'"
        ).fetchone()
        assert present >= 1, "expected 'procedure' among the ingested tokens"

        _overlay_section_skeleton(conn)

        results = list(verify("procedure", conn))
    finally:
        conn.close()

    assert len(results) == 1, f"expected exactly one Evidence, got {len(results)}"
    ev = results[0]
    assert isinstance(ev, Evidence)
    assert ev.canonical_term == "procedure"
    # Printed folio (public citation) — resolved by ingest from the real PDF.
    assert ev.folio == "1"
    # Section path emitted solely by verify() (Architecture Lock #1).
    assert ev.section_ref == "§1.01"
    assert ev.section_level == 2
    assert ev.section_path == ("§1", "§1.01")
    assert ev.match_mode == "exact"
    assert len(ev.verbatim_snippet) >= 60
    assert "procedure" in ev.verbatim_snippet.lower()


def test_verify_is_deterministic_on_public_sample(
    tmp_path: Path, sample_pdf_path: Path
) -> None:
    """D-04 contract: materializing verify() twice yields identical Evidence."""
    corpus = run_ingest(sample_pdf_path, tmp_path)
    conn = sqlite3.connect(str(corpus))
    try:
        _overlay_section_skeleton(conn)
        a = list(verify("procedure", conn))
        b = list(verify("procedure", conn))
    finally:
        conn.close()
    assert a == b
    assert set(a) == set(b)  # Evidence is frozen + hashable
