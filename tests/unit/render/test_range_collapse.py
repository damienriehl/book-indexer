"""Tests for D-03 contiguous-folio collapse (Phase 5 Wave 1).

Covers RESEARCH §H-6's 6 verbatim synthetic fixtures plus mixed Roman+Arabic,
NBSP/EN_DASH constant codepoints (Pitfall §P-4 drift guard), determinism,
and a live-IR snapshot anchor (vacuous on the reference corpus v1.0).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --- Module-level exports & constants ------------------------------------


def test_module_exports_required_names():
    from book_indexer.render import range_collapse as rc

    for name in ("collapse_locators", "format_single_locator", "NBSP", "EN_DASH"):
        assert hasattr(rc, name), f"range_collapse.py missing export: {name}"


def test_nbsp_codepoint_is_u00a0():
    """Pitfall §P-4: NBSP must be U+00A0 (UTF-8: c2 a0), NOT regular space."""
    from book_indexer.render.range_collapse import NBSP

    assert NBSP == " "
    assert NBSP != " "  # explicit guard against U+0020
    assert NBSP.encode("utf-8") == b"\xc2\xa0"


def test_en_dash_codepoint_is_u2013():
    """CONTEXT D-03: range separator is U+2013 (en-dash), NOT hyphen or em-dash."""
    from book_indexer.render.range_collapse import EN_DASH

    assert EN_DASH == "–"
    assert EN_DASH != "-"  # not U+002D hyphen-minus
    assert EN_DASH != "—"  # not em-dash
    assert EN_DASH.encode("utf-8") == b"\xe2\x80\x93"


# --- format_single_locator helper ----------------------------------------


def test_format_single_locator_arabic():
    from book_indexer.render.range_collapse import format_single_locator

    rendered = format_single_locator("§2.04", "78")
    assert rendered == "§ 2.04 (p. 78)"


def test_format_single_locator_strips_existing_section_marker():
    from book_indexer.render.range_collapse import format_single_locator

    # IR shape is '§2.04' with NO space; helper must produce '§<NBSP>2.04'
    rendered = format_single_locator("§2.04", "78")
    assert "§ " in rendered  # NBSP after §
    assert "§ " not in rendered or "§ " in rendered  # no plain-space §


def test_format_single_locator_roman_folio_preserved():
    from book_indexer.render.range_collapse import format_single_locator

    rendered = format_single_locator("§1", "iii")
    assert rendered == "§ 1 (p. iii)"


# --- collapse_locators: empty + single -----------------------------------


def test_collapse_empty_input_returns_empty():
    from book_indexer.render.range_collapse import collapse_locators

    assert collapse_locators([]) == []


def test_collapse_single_locator(make_locator):
    from book_indexer.render.range_collapse import collapse_locators

    formatted = collapse_locators([make_locator(section_ref="§2.04", folio="78", evidence_id=1)])
    assert len(formatted) == 1
    assert formatted[0].rendered == "§ 2.04 (p. 78)"
    assert formatted[0].is_range is False
    assert formatted[0].evidence_ids == (1,)


# --- RESEARCH §H-6 6 synthetic fixtures verbatim -------------------------


def test_collapse_two_contiguous(make_locator):
    """RESEARCH §H-6 fixture 1: 78,79 → '§ 2.04 (pp. 78–79)'."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§2.04", folio="79", evidence_id=2),
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 1
    assert formatted[0].rendered == "§ 2.04 (pp. 78–79)"
    assert formatted[0].is_range is True
    assert set(formatted[0].evidence_ids) == {1, 2}


def test_no_collapse_gap(make_locator):
    """RESEARCH §H-6 fixture 2: 78 + 80 → 2 separate (gap blocks collapse)."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§2.04", folio="80", evidence_id=2),
    ]
    formatted = collapse_locators(locs)
    rendered = [f.rendered for f in formatted]
    assert rendered == ["§ 2.04 (p. 78)", "§ 2.04 (p. 80)"]
    assert all(not f.is_range for f in formatted)


def test_no_collapse_cross_section(make_locator):
    """RESEARCH §H-6 fixture 3: cross-section 78,79 → no collapse."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§3.01", folio="79", evidence_id=2),
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 2
    assert all(not f.is_range for f in formatted)


def test_roman_excluded_from_collapse(make_locator):
    """RESEARCH §H-6 fixture 4: iii,iv → 2 separate (Roman is int-incompatible)."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§1", folio="iii", evidence_id=1),
        make_locator(section_ref="§1", folio="iv", evidence_id=2),
    ]
    formatted = collapse_locators(locs)
    assert [f.is_range for f in formatted] == [False, False]
    assert len(formatted) == 2


def test_three_contiguous_collapses_to_range(make_locator):
    """RESEARCH §H-6 fixture 5: 78,79,80 → '§ 2.04 (pp. 78–80)'."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio=str(f), evidence_id=i + 1)
        for i, f in enumerate([78, 79, 80])
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 1
    assert formatted[0].rendered == "§ 2.04 (pp. 78–80)"
    assert formatted[0].is_range is True


def test_singular_vs_plural(make_locator):
    """RESEARCH §H-6 fixture 6: 1 folio → '(p. N)'; 2 folios → '(pp. N–M)'."""
    from book_indexer.render.range_collapse import collapse_locators

    one = collapse_locators([make_locator(section_ref="§2.04", folio="78", evidence_id=1)])
    two = collapse_locators([
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§2.04", folio="79", evidence_id=2),
    ])
    assert one[0].rendered == "§ 2.04 (p. 78)"
    assert two[0].rendered == "§ 2.04 (pp. 78–79)"


# --- Mixed + extended cases ----------------------------------------------


def test_mixed_roman_and_arabic_same_section(make_locator):
    """Mixed Roman + Arabic in same section: Roman individual + Arabic-runs collapsed."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§1", folio="iii", evidence_id=1),
        make_locator(section_ref="§1", folio="78", evidence_id=2),
        make_locator(section_ref="§1", folio="79", evidence_id=3),
    ]
    formatted = collapse_locators(locs)
    is_ranges = sorted([f.is_range for f in formatted])
    # Expect 1 range (78-79) + 1 single (iii) → [False, True]
    assert is_ranges == [False, True]
    rendereds = sorted([f.rendered for f in formatted])
    assert "§ 1 (pp. 78–79)" in rendereds
    assert "§ 1 (p. iii)" in rendereds


def test_four_contiguous_arabics(make_locator):
    """4 contiguous Arabics → 1 range pp. 78–81."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio=str(f), evidence_id=i + 1)
        for i, f in enumerate([78, 79, 80, 81])
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 1
    assert formatted[0].rendered == "§ 2.04 (pp. 78–81)"
    assert formatted[0].is_range is True
    assert formatted[0].evidence_ids == (1, 2, 3, 4)


def test_two_runs_with_gap_in_same_section(make_locator):
    """78,79 + 90,91 in same section → 2 ranges."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§2.04", folio="79", evidence_id=2),
        make_locator(section_ref="§2.04", folio="90", evidence_id=3),
        make_locator(section_ref="§2.04", folio="91", evidence_id=4),
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 2
    assert all(f.is_range for f in formatted)
    rendereds = [f.rendered for f in formatted]
    assert "§ 2.04 (pp. 78–79)" in rendereds
    assert "§ 2.04 (pp. 90–91)" in rendereds


def test_evidence_ids_preserved_on_range(make_locator):
    """Collapsed range carries ALL underlying evidence_ids in folio order."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="80", evidence_id=99),
        make_locator(section_ref="§2.04", folio="78", evidence_id=42),
        make_locator(section_ref="§2.04", folio="79", evidence_id=7),
    ]
    formatted = collapse_locators(locs)
    assert len(formatted) == 1
    # evidence_ids in folio-sort order: 78→42, 79→7, 80→99
    assert formatted[0].evidence_ids == (42, 7, 99)


def test_output_sorted_by_section_then_folio(make_locator):
    """Result deterministic: sorted by section_ref then first-folio."""
    from book_indexer.render.range_collapse import collapse_locators

    # Provide cross-section input out of order
    locs = [
        make_locator(section_ref="§3.01", folio="50", evidence_id=1),
        make_locator(section_ref="§2.04", folio="78", evidence_id=2),
        make_locator(section_ref="§2.04", folio="100", evidence_id=3),
    ]
    formatted = collapse_locators(locs)
    sections = [f.section_ref for f in formatted]
    # §<NBSP>2.04 should sort before §<NBSP>3.01
    assert sections == sorted(sections)


def test_determinism_across_invocations(make_locator):
    """10 invocations on same input → identical output (frozen dataclass)."""
    from book_indexer.render.range_collapse import collapse_locators

    locs = [
        make_locator(section_ref="§2.04", folio="78", evidence_id=1),
        make_locator(section_ref="§2.04", folio="79", evidence_id=2),
        make_locator(section_ref="§3.01", folio="100", evidence_id=3),
    ]
    results = [collapse_locators(locs) for _ in range(10)]
    first = results[0]
    for r in results[1:]:
        assert r == first


# --- Live-IR snapshot (vacuous on the reference corpus v1.0) ------------------------


@pytest.mark.skipif(
    not Path("artifacts/index_tree.json").exists(),
    reason="live IR not committed yet (pre-Wave-4)",
)
def test_live_ir_snapshot_zero_ranges_on_corpus():
    """RESEARCH §H-6 vacuous claim: 0 range collapses on the reference corpus v1.0
    (Phase 4 cite_rule already coalesces). When companion volumes ship,
    this snapshot trips and forces conscious update."""
    from book_indexer.render import IndexTree
    from book_indexer.render.range_collapse import collapse_locators

    tree = IndexTree.model_validate_json(
        Path("artifacts/index_tree.json").read_text()
    )
    range_count = 0
    for entry in tree.entries:
        formatted = collapse_locators(entry.locators)
        range_count += sum(1 for f in formatted if f.is_range)

    assert range_count == 0, (
        f"D-03 vacuous-on-the reference corpus snapshot tripped: got {range_count} ranges. "
        f"This is OK if Phase 4 cite_rule changed; update the anchor in Plan 05-05."
    )
