"""Unit tests for src/book_indexer/render/ir.py.

Locks the Phase 5 IR contract per RESEARCH §H-12 + D-07:
  - render IR re-imports IndexTree/IndexEntry/SubEntry from
    book_indexer.assembly and Locator from book_indexer.tables.ir
    (single source of truth — D-07).
  - Two NEW frozen dataclasses Phase 5 owns:
      SyntheticEntry  (B-06 render-time projection)
      FormattedLocator (D-03 page-range collapse output)
  - Both dataclasses are frozen (FrozenInstanceError on mutation).
  - FormattedLocator.rendered preserves U+00A0 + U+2013 bytes verbatim.

Mirrors the test style of tests/unit/assembly/test_ir.py.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from book_indexer.assembly import ir as assembly_ir
from book_indexer.render import (
    FormattedLocator,
    IndexEntry,
    IndexTree,
    IndexTreeProvenance,
    Locator,
    SubEntry,
    SyntheticEntry,
)
from book_indexer.render import ir as render_ir
from book_indexer.tables import ir as tables_ir

# ---------------------------------------------------------------------------
# Re-import identity — single source of truth (D-07)
# ---------------------------------------------------------------------------


def test_locator_is_single_source_of_truth() -> None:
    """``Locator`` exposed by book_indexer.render is the SAME class as
    the one in book_indexer.tables.ir. Redefinition would break Lock #2
    (schema drift between Phase 3b/4/5)."""
    assert Locator is tables_ir.Locator


def test_locator_re_export_via_render_ir() -> None:
    """The intermediate render.ir module also re-exports the same Locator."""
    assert render_ir.Locator is tables_ir.Locator


def test_index_tree_is_assembly_class() -> None:
    """``IndexTree`` from render is the SAME class as assembly.IndexTree."""
    assert IndexTree is assembly_ir.IndexTree


def test_index_entry_is_assembly_class() -> None:
    assert IndexEntry is assembly_ir.IndexEntry


def test_sub_entry_is_assembly_class() -> None:
    assert SubEntry is assembly_ir.SubEntry


def test_index_tree_provenance_is_assembly_class() -> None:
    assert IndexTreeProvenance is assembly_ir.IndexTreeProvenance


def test_render_ir_does_not_redefine_upstream_classes() -> None:
    """``render/ir.py`` MUST NOT contain a top-level ``class Locator``,
    ``class IndexTree``, ``class IndexEntry``, or ``class SubEntry``
    declaration — only re-imports. Redefinition is a Lock #2
    ship-blocker."""
    import inspect

    source = inspect.getsource(render_ir)
    forbidden = ["class Locator", "class IndexTree", "class IndexEntry", "class SubEntry"]
    for marker in forbidden:
        assert marker not in source, (
            f"render/ir.py contains a {marker!r} declaration — "
            f"Lock #2 ship-blocker; re-import from upstream instead."
        )


# ---------------------------------------------------------------------------
# SyntheticEntry — B-06 render-time projection
# ---------------------------------------------------------------------------


def test_synthetic_entry_constructs_cleanly(make_locator) -> None:
    se = SyntheticEntry(
        stem="hearsay",
        sibling_canonicals=("admissible hearsay", "hearsay exception"),
        locators=(make_locator(folio="78"), make_locator(folio="79", evidence_id=2)),
    )
    assert se.stem == "hearsay"
    assert se.sibling_canonicals == ("admissible hearsay", "hearsay exception")
    assert len(se.locators) == 2


def test_synthetic_entry_is_frozen() -> None:
    se = SyntheticEntry(stem="hearsay", sibling_canonicals=(), locators=())
    with pytest.raises(FrozenInstanceError):
        se.stem = "other"  # type: ignore[misc]


def test_synthetic_entry_accepts_empty_collections() -> None:
    se = SyntheticEntry(stem="x", sibling_canonicals=(), locators=())
    assert se.sibling_canonicals == ()
    assert se.locators == ()


# ---------------------------------------------------------------------------
# FormattedLocator — D-03 page-range collapse output
# ---------------------------------------------------------------------------


def test_formatted_locator_constructs_cleanly() -> None:
    fl = FormattedLocator(
        section_ref="§ 2.04",
        rendered="§ 2.04 (p. 78)",
        is_range=False,
        evidence_ids=(1,),
    )
    assert fl.section_ref == "§ 2.04"
    assert fl.rendered == "§ 2.04 (p. 78)"
    assert fl.is_range is False
    assert fl.evidence_ids == (1,)


def test_formatted_locator_is_frozen() -> None:
    fl = FormattedLocator(
        section_ref="§ 2.04", rendered="§ 2.04 (p. 78)", is_range=False, evidence_ids=(1,)
    )
    with pytest.raises(FrozenInstanceError):
        fl.rendered = "altered"  # type: ignore[misc]


def test_formatted_locator_preserves_nbsp_and_endash_bytes() -> None:
    """D-03: rendered string carries U+00A0 (nbsp) between § and N and
    U+2013 (endash) in page ranges. Verify byte-level preservation."""
    rendered = "§ 2.04 (pp. 78–80)"
    fl = FormattedLocator(
        section_ref="§ 2.04",
        rendered=rendered,
        is_range=True,
        evidence_ids=(1, 2, 3),
    )
    assert fl.rendered == rendered
    # Spot-check the actual code points survived without normalization.
    assert " " in fl.rendered
    assert "–" in fl.rendered
    assert fl.is_range is True
    assert fl.evidence_ids == (1, 2, 3)


def test_formatted_locator_singular_vs_range_flag() -> None:
    """``is_range`` distinguishes ``(p. N)`` from ``(pp. N–M)``."""
    singular = FormattedLocator(
        section_ref="§ 1.01", rendered="§ 1.01 (p. 5)", is_range=False, evidence_ids=(1,)
    )
    plural = FormattedLocator(
        section_ref="§ 1.01",
        rendered="§ 1.01 (pp. 5–7)",
        is_range=True,
        evidence_ids=(1, 2, 3),
    )
    assert not singular.is_range
    assert plural.is_range


# ---------------------------------------------------------------------------
# Smoke: render IR module exposes exactly the expected dataclasses
# ---------------------------------------------------------------------------


def test_render_ir_exposes_two_new_dataclasses() -> None:
    """RESEARCH §H-12 module layout: render/ir.py owns exactly two new
    dataclasses (SyntheticEntry, FormattedLocator). Anything else is drift."""
    import dataclasses
    import inspect

    new_dataclasses = [
        name
        for name, obj in inspect.getmembers(render_ir, inspect.isclass)
        if dataclasses.is_dataclass(obj) and obj.__module__ == render_ir.__name__
    ]
    assert sorted(new_dataclasses) == ["FormattedLocator", "SyntheticEntry"]
