"""Phase 9 Wave 2 — per-rule unit test for R7 fold doubled-word artifact.

Worked example (RESEARCH §"Per-R-class Implementation Notes" R7):
  entries = [
    IndexEntry(canonical="fact finder", locators=[L1, L2]),
    IndexEntry(canonical="fact finder fact finder", locators=[L3]),
  ]
  rule = R7FoldDoubledWordRule(artifact="fact finder fact finder",
                                canonical="fact finder")
  → first apply: artifact dropped; target's locators = sorted union of L1, L2, L3.
  → second apply (allow_stale=True): silent no-op.
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import (
    EditorialOverrides,
    R7FoldDoubledWordRule,
)
from book_indexer.render.editorial_overrides import (
    EditorialOverrideMismatch,
    apply_editorial_overrides,
)
from book_indexer.render.ir import IndexEntry
from book_indexer.tables.ir import Locator


_SIGNED_META = {
    "schema_version": 1,
    "curated_by": "test@example.com",
    "curated_at_iso": "2026-05-05T00:00:00Z",
    "source_index_version": "1.2.0",
    "source_index_sha256": "",
}


def _slug(canonical: str) -> str:
    return canonical.replace(" ", "-").replace("'", "").lower()


def _loc(section: str, folio: str, evidence_id: int) -> Locator:
    return Locator(section_ref=section, folio=folio, evidence_id=evidence_id)


def _make_entry(canonical: str, *, locators: list[Locator] | None = None) -> IndexEntry:
    return IndexEntry(
        id=_slug(canonical),
        canonical=canonical,
        sort_key=canonical.lower(),
        locators=locators or [],
    )


def _fix(rules: list[R7FoldDoubledWordRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {
            "metadata": _SIGNED_META,
            "R7_fold_doubled_word": [r.model_dump() for r in rules],
        }
    )


def test_R7_happy_path_merges_artifact_locators_into_target() -> None:
    L1 = _loc("§1.01", "10", 1)
    L2 = _loc("§1.02", "11", 2)
    L3 = _loc("§2.01", "20", 3)
    entries = [
        _make_entry("fact finder", locators=[L1, L2]),
        _make_entry("fact finder fact finder", locators=[L3]),
    ]
    fixture = _fix(
        [
            R7FoldDoubledWordRule(
                artifact="fact finder fact finder", canonical="fact finder"
            )
        ]
    )
    result = apply_editorial_overrides(entries, fixture)
    assert [e.canonical for e in result.entries] == ["fact finder"]
    target = result.entries[0]
    # Sorted union by (section_ref, folio); no duplicates.
    locator_keys = [(lo.section_ref, lo.folio) for lo in target.locators]
    assert locator_keys == [("§1.01", "10"), ("§1.02", "11"), ("§2.01", "20")]
    assert result.mismatches == ()


def test_R7_idempotence_under_allow_stale() -> None:
    L1 = _loc("§1.01", "10", 1)
    L3 = _loc("§2.01", "20", 3)
    entries = [
        _make_entry("fact finder", locators=[L1]),
        _make_entry("fact finder fact finder", locators=[L3]),
    ]
    fixture = _fix(
        [
            R7FoldDoubledWordRule(
                artifact="fact finder fact finder", canonical="fact finder"
            )
        ]
    )
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]
    once_keys = [(lo.section_ref, lo.folio) for lo in once.entries[0].locators]
    twice_keys = [(lo.section_ref, lo.folio) for lo in twice.entries[0].locators]
    assert once_keys == twice_keys


def test_R7_mismatch_first_apply_raises() -> None:
    """Target absent → mismatch."""
    entries = [_make_entry("witness")]
    fixture = _fix(
        [
            R7FoldDoubledWordRule(
                artifact="fact finder fact finder", canonical="fact finder"
            )
        ]
    )
    with pytest.raises(EditorialOverrideMismatch):
        apply_editorial_overrides(entries, fixture, allow_stale=False)


def test_R7_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix(
        [
            R7FoldDoubledWordRule(
                artifact="fact finder fact finder", canonical="fact finder"
            )
        ]
    )
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert len(result.mismatches) == 1


def test_R7_locator_merge_dedup_sorted() -> None:
    """Overlapping locators are deduped; result is sorted by (section_ref, folio)."""
    shared = _loc("§1.05", "5", 99)
    target_locs = [_loc("§1.02", "2", 1), shared]
    artifact_locs = [shared, _loc("§1.01", "1", 2)]  # unsorted on purpose
    entries = [
        _make_entry("fact finder", locators=target_locs),
        _make_entry("fact finder fact finder", locators=artifact_locs),
    ]
    fixture = _fix(
        [
            R7FoldDoubledWordRule(
                artifact="fact finder fact finder", canonical="fact finder"
            )
        ]
    )
    result = apply_editorial_overrides(entries, fixture)
    target = result.entries[0]
    keys = [(lo.section_ref, lo.folio) for lo in target.locators]
    assert keys == [("§1.01", "1"), ("§1.02", "2"), ("§1.05", "5")]
    # Dedup: shared locator appears exactly once.
    assert keys.count(("§1.05", "5")) == 1
