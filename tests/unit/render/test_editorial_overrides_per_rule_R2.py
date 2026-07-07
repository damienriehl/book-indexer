"""Phase 9 Wave 2 — per-rule unit test for R2 recapitalize.

Worked example (RESEARCH §"Per-R-class Implementation Notes" R2):
  IndexEntry(canonical="frcp") + R2RecapitalizeRule(wrong="frcp", right="FRCP")
  → first apply: canonical == "FRCP"
  → second apply (allow_stale=True): silent no-op (rule.right already in canonicals).
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import EditorialOverrides, R2RecapitalizeRule
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


def _slug(canonical: str) -> str:
    return canonical.replace(" ", "-").replace("'", "").lower()


def _make_entry(canonical: str) -> IndexEntry:
    return IndexEntry(
        id=_slug(canonical),
        canonical=canonical,
        sort_key=canonical.lower(),
        locators=[],
    )


def _fix(rules: list[R2RecapitalizeRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {"metadata": _SIGNED_META, "R2_recapitalize": [r.model_dump() for r in rules]}
    )


def test_R2_happy_path_recapitalizes_canonical() -> None:
    entries = [_make_entry("frcp")]
    fixture = _fix([R2RecapitalizeRule(wrong="frcp", right="FRCP")])
    result = apply_editorial_overrides(entries, fixture)
    assert [e.canonical for e in result.entries] == ["FRCP"]
    assert result.mismatches == ()


def test_R2_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("frcp")]
    fixture = _fix([R2RecapitalizeRule(wrong="frcp", right="FRCP")])
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]


def test_R2_mismatch_first_apply_raises() -> None:
    entries = [_make_entry("trial")]
    fixture = _fix([R2RecapitalizeRule(wrong="frcp", right="FRCP")])
    with pytest.raises(EditorialOverrideMismatch):
        apply_editorial_overrides(entries, fixture, allow_stale=False)


def test_R2_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("trial")]
    fixture = _fix([R2RecapitalizeRule(wrong="frcp", right="FRCP")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["trial"]
    assert len(result.mismatches) == 1
    assert result.mismatches[0].rule_class == "R2_recapitalize"
