"""Phase 9 — D-01 locked apply order regression test.

The compile-time tuple ``_APPLY_ORDER`` MUST be exactly:

    R4 → R5 → R6 → R7 → R3 → R8 → R2 → R1 → R9

Any change to this sequence requires updating CONTEXT.md D-01 first;
this test catches accidental reorderings during refactors.
"""
from __future__ import annotations

from book_indexer.render.editorial_overrides import _APPLY_ORDER


def test_apply_order_is_d01_sequence() -> None:
    expected = (
        "R4_delete_entry",
        "R5_delete_xref",
        "R6_promote_single_child",
        "R7_fold_doubled_word",
        "R3_reword",
        "R8_plural_canonical",
        "R2_recapitalize",
        "R1_strip_variants",
        "R9_whitespace",
    )
    assert _APPLY_ORDER == expected, (
        f"D-01 violation: _APPLY_ORDER changed.\n"
        f"  Expected: {expected}\n"
        f"  Got:      {_APPLY_ORDER}"
    )


def test_apply_order_has_exactly_9_classes() -> None:
    assert len(_APPLY_ORDER) == 9
    assert len(set(_APPLY_ORDER)) == 9, "duplicate R-class in _APPLY_ORDER"
