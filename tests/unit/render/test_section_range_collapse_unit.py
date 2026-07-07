"""Phase 7 / OUT-05 — section_range_collapse unit tests (Wave 3 Task 1).

Covers the 9 cases enumerated in Plan 07-03 Task 1 ``<behavior>``:

  1. Same chapter, consecutive minor → ``§§ N.NN–M.MM``.
  2. Same major, consecutive sub    → ``§§ N.NN.x–N.NN.y``.
  3. Skip (gap)                     → 2 separate ``§ N.NN`` outputs.
  4. Cross-tier                     → 2 separate outputs (no collapse).
  5. Cross-chapter                  → 2 separate outputs.
  6. Run of ≥3                      → single range, NOT pairwise.
  7. Single locator                 → ``§ N.NN`` (singular ``§``).
  8. Empty input                    → ``[]``.
  9. Metadata pages_only_variant    → defaults to ``False``; settable to ``True``.

Pitfall §P-4: NBSP / EN DASH constants only — NEVER ASCII space or hyphen.
"""
from __future__ import annotations

import pytest

from book_indexer.render.section_range_collapse import (
    EN_DASH,
    NBSP,
    collapse_locators_sections_only,
)
from book_indexer.tables.ir import Locator


# Helper — minimal Locator construction. Locator requires evidence_id >= 1.
def _loc(section_ref: str, folio: str, evidence_id: int = 1) -> Locator:
    return Locator(section_ref=section_ref, folio=folio, evidence_id=evidence_id)


# --- Module-level constants --------------------------------------------


def test_nbsp_codepoint_is_u00a0():
    """Pitfall §P-4 — NBSP is U+00A0 (UTF-8 ``c2 a0``), NEVER U+0020."""
    assert NBSP == " "
    assert NBSP.encode("utf-8") == b"\xc2\xa0"


def test_en_dash_codepoint_is_u2013():
    """CONTEXT D-03 — range separator is U+2013 EN DASH, NEVER hyphen / em-dash."""
    assert EN_DASH == "–"
    assert EN_DASH != "-"
    assert EN_DASH != "—"
    assert EN_DASH.encode("utf-8") == b"\xe2\x80\x93"


# --- Test 1-7: collapse rules ------------------------------------------


def test_1_same_chapter_consecutive_minor_collapses_to_range():
    """``§2.04 + §2.05`` → ``§§ 2.04–2.05`` (single range FormattedLocator)."""
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§2.05", "20", 2)]
    )
    assert len(out) == 1
    assert out[0].is_range is True
    assert out[0].rendered == f"§§{NBSP}2.04{EN_DASH}2.05"


def test_2_same_major_consecutive_sub_collapses_to_range():
    """``§2.04.1 + §2.04.2`` → ``§§ 2.04.1–2.04.2`` (sub-tier collapse)."""
    out = collapse_locators_sections_only(
        [_loc("§2.04.1", "65", 1), _loc("§2.04.2", "66", 2)]
    )
    assert len(out) == 1
    assert out[0].is_range is True
    assert out[0].rendered == f"§§{NBSP}2.04.1{EN_DASH}2.04.2"


def test_3_skip_gap_no_collapse():
    """``§2.04 + §2.06`` → 2 outputs (gap; no collapse)."""
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§2.06", "21", 2)]
    )
    assert len(out) == 2
    rendered = [fl.rendered for fl in out]
    assert rendered == [f"§{NBSP}2.04", f"§{NBSP}2.06"]
    assert all(fl.is_range is False for fl in out)


def test_4_cross_tier_no_collapse():
    """``§2.04 + §2.05.1`` → 2 outputs (different depth → no collapse)."""
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§2.05.1", "22", 2)]
    )
    assert len(out) == 2
    rendered = [fl.rendered for fl in out]
    assert rendered == [f"§{NBSP}2.04", f"§{NBSP}2.05.1"]


def test_5_cross_chapter_no_collapse():
    """``§2.04 + §3.01`` → 2 outputs (cross-chapter; never collapses)."""
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§3.01", "95", 2)]
    )
    assert len(out) == 2
    rendered = [fl.rendered for fl in out]
    assert rendered == [f"§{NBSP}2.04", f"§{NBSP}3.01"]


def test_6_run_of_three_or_more_emits_single_range_not_pairwise():
    """``§2.04 + §2.05 + §2.06`` → single ``§§ 2.04–2.06`` (NOT pairwise chain)."""
    out = collapse_locators_sections_only(
        [
            _loc("§2.04", "19", 1),
            _loc("§2.05", "20", 2),
            _loc("§2.06", "21", 3),
        ]
    )
    assert len(out) == 1
    assert out[0].is_range is True
    assert out[0].rendered == f"§§{NBSP}2.04{EN_DASH}2.06"


def test_7_single_locator_emits_singular_section_sigil():
    """Single locator → ``§ 2.04`` (singular sigil, not ``§§``)."""
    out = collapse_locators_sections_only([_loc("§2.04", "19", 1)])
    assert len(out) == 1
    assert out[0].is_range is False
    assert out[0].rendered == f"§{NBSP}2.04"


def test_8_empty_input_returns_empty_list():
    """Empty iterable → ``[]``."""
    out = collapse_locators_sections_only([])
    assert out == []


# --- Additional coverage: deduplication + sort determinism --------------


def test_same_section_different_folios_dedupe_to_one():
    """Sections-only output emits each section_ref ONCE.

    Two locators with the same ``section_ref`` but different folios
    deduplicate to a single ``§ 2.04`` (folio differences are projected
    out in sections-only output).
    """
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§2.04", "20", 2)]
    )
    assert len(out) == 1
    assert out[0].rendered == f"§{NBSP}2.04"


def test_unsorted_input_sorted_deterministically():
    """Input order does not matter — output sorted by parsed int-tuple."""
    out_a = collapse_locators_sections_only(
        [_loc("§2.05", "20", 1), _loc("§2.04", "19", 2)]
    )
    out_b = collapse_locators_sections_only(
        [_loc("§2.04", "19", 2), _loc("§2.05", "20", 1)]
    )
    assert [fl.rendered for fl in out_a] == [fl.rendered for fl in out_b]


def test_no_ascii_space_in_rendered_output():
    """Pitfall §P-4 — rendered locators must use NBSP, never ASCII space."""
    out = collapse_locators_sections_only(
        [_loc("§2.04", "19", 1), _loc("§2.05", "20", 2)]
    )
    # Only ASCII space in rendered should be after the section sigil — but
    # we use NBSP, so there should be ZERO ASCII spaces.
    for fl in out:
        assert " " not in fl.rendered, (
            f"ASCII space found in rendered locator: {fl.rendered!r} "
            f"(must use NBSP U+00A0)"
        )


# --- Test 9: Metadata pages_only_variant --------------------------------


def test_9_metadata_pages_only_variant_field_default_false():
    """``Metadata.pages_only_variant`` defaults to ``False``."""
    from book_indexer.render.metadata import Metadata

    m = Metadata(
        pdf_sha256="0" * 64,
        pipeline_version="1.0.0",
        index_tree_schema_version="1.0",
        eyecite_version="2.7.6",
        reporters_db_version="3.2.65",
        courts_db_version="0.10.20",
        spacy_version="3.8.0",
        spacy_model_sha="0" * 64,
        pymupdf_version="1.27.2.2",
        python_docx_version="1.2.0",
        cli_version="claude (test)",
    )
    assert m.pages_only_variant is False


def test_9_metadata_pages_only_variant_settable_to_true():
    """``Metadata.pages_only_variant`` accepts ``True`` for sections-only variant."""
    from book_indexer.render.metadata import Metadata

    m = Metadata(
        pdf_sha256="0" * 64,
        pipeline_version="1.0.0",
        index_tree_schema_version="1.0",
        eyecite_version="2.7.6",
        reporters_db_version="3.2.65",
        courts_db_version="0.10.20",
        spacy_version="3.8.0",
        spacy_model_sha="0" * 64,
        pymupdf_version="1.27.2.2",
        python_docx_version="1.2.0",
        cli_version="claude (test)",
        pages_only_variant=True,
    )
    assert m.pages_only_variant is True


def test_9_metadata_extra_forbid_still_enforced():
    """Lock #2 — ``extra='forbid'`` still rejects unknown fields after the
    pages_only_variant addition."""
    from pydantic import ValidationError
    from book_indexer.render.metadata import Metadata

    with pytest.raises(ValidationError):
        Metadata(
            pdf_sha256="0" * 64,
            pipeline_version="1.0.0",
            index_tree_schema_version="1.0",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.65",
            courts_db_version="0.10.20",
            spacy_version="3.8.0",
            spacy_model_sha="0" * 64,
            pymupdf_version="1.27.2.2",
            python_docx_version="1.2.0",
            cli_version="claude (test)",
            unexpected_field="x",  # type: ignore[call-arg]
        )


# --- Sections-only renderer module surface (smoke) ----------------------


def test_markdown_sections_only_module_exposes_render_function():
    from book_indexer.render import markdown_sections_only as mod

    assert hasattr(mod, "render_markdown_sections_only")
    assert callable(mod.render_markdown_sections_only)


def test_docx_sections_only_module_exposes_render_function():
    from book_indexer.render import docx_sections_only as mod

    assert hasattr(mod, "render_docx_sections_only")
    assert callable(mod.render_docx_sections_only)
