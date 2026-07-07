"""Phase 9 Wave 2 — per-rule unit test for R6 promote single child.

R6 is an IR no-op; it emits ``rule.parent_stem`` onto the
``ApplyPassResult.synth_suppressed_stems`` side-channel, which the renderer
consumes when filtering synthetics from the merged stream.

Like R5, mismatch detection is deferred to the renderer (the apply pass
cannot know whether a synth stem exists). The 4 standard cases exercise
the side-channel contract.
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import (
    EditorialOverrides,
    R6PromoteSingleChildRule,
)
from book_indexer.render.editorial_overrides import (
    EditorialOverrideMismatch,
    apply_editorial_overrides,
)
from book_indexer.render.ir import IndexEntry


_SIGNED_META = {
    "schema_version": 1,
    "curated_by": "test@example.com",
    "curated_at_iso": "2026-05-05T00:00:00Z",
    "source_index_version": "1.2.0",
    "source_index_sha256": "",
}


def _make_entry(canonical: str) -> IndexEntry:
    return IndexEntry(
        id=canonical.replace(" ", "-").lower(),
        canonical=canonical,
        sort_key=canonical.lower(),
        locators=[],
    )


def _fix(rules: list[R6PromoteSingleChildRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {
            "metadata": _SIGNED_META,
            "R6_promote_single_child": [r.model_dump() for r in rules],
        }
    )


def test_R6_happy_path_emits_stem_to_side_channel() -> None:
    entries = [_make_entry("hearing")]
    fixture = _fix([R6PromoteSingleChildRule(parent_stem="hear")])
    result = apply_editorial_overrides(entries, fixture)
    assert result.synth_suppressed_stems == frozenset({"hear"})
    assert [e.canonical for e in result.entries] == ["hearing"]
    assert result.mismatches == ()


def test_R6_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("hearing")]
    fixture = _fix([R6PromoteSingleChildRule(parent_stem="hear")])
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert once.synth_suppressed_stems == twice.synth_suppressed_stems


def test_R6_mismatch_first_apply_does_not_raise() -> None:
    """R6 mismatch detection is deferred (renderer-time)."""
    entries = [_make_entry("witness")]
    fixture = _fix([R6PromoteSingleChildRule(parent_stem="nonexistent_stem")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=False)
    assert "nonexistent_stem" in result.synth_suppressed_stems


def test_R6_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R6PromoteSingleChildRule(parent_stem="hear")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert result.synth_suppressed_stems == frozenset({"hear"})
