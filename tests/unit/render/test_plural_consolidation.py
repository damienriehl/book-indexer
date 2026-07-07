"""v1.2.1 unit tests for render/plural_consolidation.py.

Covers the user-locked decisions:

  * Format: ``term(s)`` / ``term(es)`` / ``term(ies)``.
  * Scope: cross-refs AND primary entries (most-aggressive mode).
  * Stop-list: ``keep_plural_variants`` from the Phase 7 fixture
    (here passed in directly as a frozenset).
  * Conservative merge rules:
      - Skip if either side is in the stop-list.
      - Skip primary pairs with non-matching locators.
      - Skip primary pairs whose variants are non-empty.
      - Skip pairs whose plural is irregular (criterion/criteria).
      - Skip xref pairs pointing at different See targets.

Lock #1 preserved: this module never imports ``verify``.
Lock #5: idempotent (consolidating twice == once).

requirements_addressed: v1.2.1 plural consolidation.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from book_indexer.assembly.ir import IndexEntry
from book_indexer.render.cross_refs import CrossRefEntry
from book_indexer.render.plural_consolidation import (
    ConsolidatedEntry,
    DEFAULT_KEEP_PLURAL_VARIANTS,
    consolidate_plural_pairs,
    infer_inflection_ending,
)
from book_indexer.tables.ir import Locator


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _loc(section_ref: str = "§1.01", folio: str = "1", evidence_id: int = 1) -> Locator:
    return Locator(section_ref=section_ref, folio=folio, evidence_id=evidence_id)


def _entry(
    canonical: str,
    *,
    sort_key: str | None = None,
    locators: list[Locator] | None = None,
    variants: list[str] | None = None,
    sub_entries: list = None,
) -> IndexEntry:
    sk = sort_key if sort_key is not None else canonical.lower()
    slug = canonical.lower().replace(" ", "-")
    return IndexEntry(
        id=slug,
        canonical=canonical,
        sort_key=sk,
        locators=locators if locators is not None else [_loc()],
        variants=variants or [],
        sub_entries=sub_entries or [],
    )


def _xref(head: str, target: str) -> CrossRefEntry:
    return CrossRefEntry(head=head, primary_canonical=target, sort_key=head.lower())


def _entry_item(entry: IndexEntry):
    return (entry.sort_key, "entry", entry)


def _xref_item(xref: CrossRefEntry):
    return (xref.sort_key, "xref", xref)


# ---------------------------------------------------------------------------
# 1. Inflection-ending inference
# ---------------------------------------------------------------------------


def test_inflection_ending_simple_s() -> None:
    assert infer_inflection_ending("dog", "dogs") == "(s)"


def test_inflection_ending_es() -> None:
    assert infer_inflection_ending("bus", "buses") == "(es)"


def test_inflection_ending_y_ies() -> None:
    assert infer_inflection_ending("agency", "agencies") == "(ies)"
    assert infer_inflection_ending("copy", "copies") == "(ies)"


def test_inflection_ending_irregular_returns_none() -> None:
    # 'criteria' is the irregular plural of 'criterion' — no regular ending.
    assert infer_inflection_ending("criterion", "criteria") is None
    assert infer_inflection_ending("analysis", "analyses") is None


def test_inflection_ending_case_tolerant() -> None:
    # The function lowercases internally; non-lowercase input still works.
    assert infer_inflection_ending("Agency", "Agencies") == "(ies)"


# ---------------------------------------------------------------------------
# 2. Cross-ref consolidation — the user's primary use-case
# ---------------------------------------------------------------------------


def test_xref_pair_consolidates_to_communications() -> None:
    items = [
        _xref_item(_xref("communication", "privileged communication")),
        _xref_item(_xref("communications", "privileged communication")),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 1
    sort_key, kind, payload = out[0]
    assert kind == "consolidated"
    assert isinstance(payload, ConsolidatedEntry)
    assert payload.display_canonical == "communication(s)"
    assert payload.see_target == "privileged communication"
    assert payload.source_kind == "xref"


def test_xref_pair_consolidates_to_agencies_ies() -> None:
    """Bidirectional detection: y → ies plurals sort BEFORE the singular
    alphabetically (``agencies`` < ``agency`` because i < y), so the
    plural can appear first in the merged stream. The consolidator
    must detect the pair regardless of order and always render with
    the singular's spelling intact."""
    items = [
        _xref_item(_xref("agencies", "administrative agency")),
        _xref_item(_xref("agency", "administrative agency")),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 1
    payload = out[0][2]
    assert payload.display_canonical == "agency(ies)"
    assert payload.primary_canonical == "agency"
    assert payload.see_target == "administrative agency"

    # Same when the order is reversed (singular first).
    items2 = [
        _xref_item(_xref("agency", "administrative agency")),
        _xref_item(_xref("agencies", "administrative agency")),
    ]
    out2 = consolidate_plural_pairs(items2, keep_plural_set=frozenset())
    assert len(out2) == 1
    assert out2[0][2].display_canonical == "agency(ies)"


def test_xref_pair_different_targets_preserved_separately() -> None:
    # Same singular/plural pair but pointing at different canonicals —
    # the consolidator must NOT collapse (would be ambiguous).
    items = [
        _xref_item(_xref("agency", "administrative agency")),
        _xref_item(_xref("agencies", "regulatory agency")),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 2
    assert all(t[1] == "xref" for t in out)


# ---------------------------------------------------------------------------
# 3. Primary-entry consolidation
# ---------------------------------------------------------------------------


def test_primary_pair_consolidates_when_locators_match() -> None:
    locs = [_loc("§2.04", "19")]
    items = [
        _entry_item(_entry("witness", locators=locs)),
        _entry_item(_entry("witnesses", locators=locs)),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 1
    payload = out[0][2]
    assert isinstance(payload, ConsolidatedEntry)
    assert payload.display_canonical == "witness(es)"
    assert payload.source_kind == "primary"
    assert payload.see_target is None
    assert payload.locators == tuple(locs)


def test_primary_pair_skipped_when_locators_differ() -> None:
    items = [
        _entry_item(_entry("witness", locators=[_loc("§2.04", "19")])),
        _entry_item(_entry("witnesses", locators=[_loc("§3.05", "84")])),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 2
    assert all(t[1] == "entry" for t in out)


def test_primary_pair_skipped_when_variants_present() -> None:
    locs = [_loc("§2.04", "19")]
    items = [
        _entry_item(_entry("claim", locators=locs, variants=["claiming"])),
        _entry_item(_entry("claims", locators=locs)),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 2


def test_primary_pair_skipped_when_subentries_present() -> None:
    from book_indexer.assembly.ir import SubEntry as AssemblySubEntry

    locs = [_loc("§2.04", "19")]
    sub = AssemblySubEntry(
        text="case law", sort_key="case law", locators=locs
    )
    items = [
        _entry_item(_entry("case", locators=locs, sub_entries=[sub])),
        _entry_item(_entry("cases", locators=locs)),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 4. Stop-list (curator-protected legally-distinct plurals) — the most
# important anti-regression: damage / damages MUST stay separate.
# ---------------------------------------------------------------------------


def test_stop_list_blocks_damage_damages_collapse() -> None:
    """Anti-regression: ``damages`` is in the curator's
    keep_plural_variants because the legal meaning differs from
    ``damage``. The two must NEVER be consolidated."""
    locs = [_loc("§2.04", "19")]
    items = [
        _entry_item(_entry("damage", locators=locs)),
        _entry_item(_entry("damages", locators=locs)),
    ]
    out = consolidate_plural_pairs(
        items, keep_plural_set=frozenset({"damages"})
    )
    assert len(out) == 2
    assert all(t[1] == "entry" for t in out)


def test_stop_list_uses_default_set_for_findings() -> None:
    locs = [_loc("§2.04", "19")]
    items = [
        _entry_item(_entry("finding", locators=locs)),
        _entry_item(_entry("findings", locators=locs)),
    ]
    out = consolidate_plural_pairs(
        items, keep_plural_set=DEFAULT_KEEP_PLURAL_VARIANTS
    )
    assert len(out) == 2


def test_stop_list_protects_costs() -> None:
    locs = [_loc("§2.04", "19")]
    items = [
        _entry_item(_entry("cost", locators=locs)),
        _entry_item(_entry("costs", locators=locs)),
    ]
    out = consolidate_plural_pairs(
        items, keep_plural_set=DEFAULT_KEEP_PLURAL_VARIANTS
    )
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 5. Irregular-plural skip
# ---------------------------------------------------------------------------


def test_irregular_plural_skipped() -> None:
    # criterion → criteria — irregular, skip
    locs = [_loc()]
    items = [
        _entry_item(_entry("criterion", locators=locs)),
        _entry_item(_entry("criteria", locators=locs)),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    # The algorithm uses inflect.singular_noun + infer_inflection_ending.
    # Even if inflect treats criterion/criteria as a singular/plural pair
    # at the ID level, infer_inflection_ending returns None for the
    # irregular ending, so the pair is preserved.
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 6. Alphabetical / passthrough preservation
# ---------------------------------------------------------------------------


def test_passthrough_preserves_unrelated_items() -> None:
    locs = [_loc()]
    items = [
        _entry_item(_entry("aardvark", locators=locs)),
        _xref_item(_xref("communication", "privileged communication")),
        _xref_item(_xref("communications", "privileged communication")),
        _entry_item(_entry("zebra", locators=locs)),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    # 4 → 3: aardvark, consolidated communication(s), zebra.
    assert len(out) == 3
    assert out[0][2].canonical == "aardvark"
    assert out[1][1] == "consolidated"
    assert out[1][2].display_canonical == "communication(s)"
    assert out[2][2].canonical == "zebra"


def test_mixed_kinds_not_consolidated() -> None:
    # An entry adjacent to a cross-ref must NOT consolidate even if the
    # canonicals are a singular/plural pair — different kinds carry
    # different render semantics (locators vs See target).
    locs = [_loc()]
    entry = _entry("communication", locators=locs)
    xref = _xref("communications", "privileged communication")
    items = [_entry_item(entry), _xref_item(xref)]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 7. Idempotence (Lock #5 — Hypothesis property)
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=0, max_value=20),
    seed=st.integers(min_value=0, max_value=1_000_000),
)
@settings(max_examples=30, deadline=None)
def test_idempotent_consolidation(n: int, seed: int) -> None:
    """Consolidating twice == once (the second pass finds no pairs to
    collapse because the previous pass already converted them to
    ``"consolidated"`` items, and consolidated items are not eligible
    for further consolidation)."""
    import random

    rng = random.Random(seed)
    candidates = [
        ("communication", "communications", "privileged communication"),
        ("agency", "agencies", "administrative agency"),
        ("copy", "copies", "accurate copy"),
        ("characteristic", "characteristics", "distinctive characteristic"),
    ]
    items: list = []
    for _ in range(n):
        head, plural, target = rng.choice(candidates)
        items.append(_xref_item(_xref(head, target)))
        items.append(_xref_item(_xref(plural, target)))
    once = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    twice = consolidate_plural_pairs(once, keep_plural_set=frozenset())
    assert once == twice


# ---------------------------------------------------------------------------
# 8. Exact format spot-checks (user-locked decisions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "singular,plural,expected",
    [
        ("communication", "communications", "communication(s)"),
        ("agency", "agencies", "agency(ies)"),
        ("bus", "buses", "bus(es)"),
        ("copy", "copies", "copy(ies)"),
        ("characteristic", "characteristics", "characteristic(s)"),
    ],
)
def test_consolidation_format_examples(
    singular: str, plural: str, expected: str
) -> None:
    items = [
        _xref_item(_xref(singular, "primary canonical")),
        _xref_item(_xref(plural, "primary canonical")),
    ]
    out = consolidate_plural_pairs(items, keep_plural_set=frozenset())
    assert len(out) == 1
    assert out[0][2].display_canonical == expected
