"""Phase 9 Wave 2 — per-rule unit test for R8 plural canonical.

Worked example (RESEARCH §"Per-R-class Implementation Notes" R8):
  IndexEntry(canonical="finding") +
  R8PluralCanonicalRule(singular="finding", plural="findings")
  → first apply: canonical == "findings"
  → second apply (allow_stale=True): silent no-op.
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import (
    EditorialOverrides,
    R8PluralCanonicalRule,
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


def _fix(rules: list[R8PluralCanonicalRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {
            "metadata": _SIGNED_META,
            "R8_plural_canonical": [r.model_dump() for r in rules],
        }
    )


def test_R8_happy_path_renames_to_plural() -> None:
    entries = [_make_entry("finding")]
    fixture = _fix([R8PluralCanonicalRule(singular="finding", plural="findings")])
    result = apply_editorial_overrides(entries, fixture)
    assert [e.canonical for e in result.entries] == ["findings"]
    assert result.mismatches == ()


def test_R8_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("finding")]
    fixture = _fix([R8PluralCanonicalRule(singular="finding", plural="findings")])
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]


def test_R8_mismatch_first_apply_raises() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R8PluralCanonicalRule(singular="finding", plural="findings")])
    with pytest.raises(EditorialOverrideMismatch):
        apply_editorial_overrides(entries, fixture, allow_stale=False)


def test_R8_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R8PluralCanonicalRule(singular="finding", plural="findings")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert len(result.mismatches) == 1
    assert result.mismatches[0].rule_class == "R8_plural_canonical"
