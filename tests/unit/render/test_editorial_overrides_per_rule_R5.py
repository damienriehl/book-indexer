"""Phase 9 Wave 2 — per-rule unit test for R5 delete cross-ref.

R5 is an IR no-op; it emits the rule.head onto the
``ApplyPassResult.xref_removal_set`` side-channel, which the renderer
consumes when filtering ``derive_cross_refs`` output.

R5 has no first-apply mismatch detection at this layer — the renderer is
the source of truth for whether a head exists in the cross-ref set
(mismatches per RESEARCH §"Per-R-class Implementation Notes" R5 are
deferred to renderer integration tests in Wave 4 byte-identity gate).
The 4 standard cases here exercise the side-channel contract.
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import EditorialOverrides, R5DeleteXrefRule
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


def _fix(rules: list[R5DeleteXrefRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {"metadata": _SIGNED_META, "R5_delete_xref": [r.model_dump() for r in rules]}
    )


def test_R5_happy_path_emits_head_to_side_channel() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R5DeleteXrefRule(head="four(s)")])
    result = apply_editorial_overrides(entries, fixture)
    assert result.xref_removal_set == frozenset({"four(s)"})
    # Entries pass through unchanged.
    assert [e.canonical for e in result.entries] == ["witness"]
    assert result.mismatches == ()


def test_R5_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R5DeleteXrefRule(head="four(s)")])
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert once.xref_removal_set == twice.xref_removal_set
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]


def test_R5_mismatch_first_apply_does_not_raise() -> None:
    """R5 mismatch detection is deferred to the renderer (the apply pass
    cannot know whether a head exists in the cross-ref set). Side-channel
    is always populated; allow_stale=False does not raise on R5 alone.
    """
    entries = [_make_entry("witness")]
    fixture = _fix([R5DeleteXrefRule(head="nonexistent_head")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=False)
    assert "nonexistent_head" in result.xref_removal_set


def test_R5_escape_hatch_skip_with_allow_stale() -> None:
    """allow_stale=True is a strict superset of False for R5 — same output."""
    entries = [_make_entry("witness")]
    fixture = _fix([R5DeleteXrefRule(head="four(s)")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert result.xref_removal_set == frozenset({"four(s)"})
