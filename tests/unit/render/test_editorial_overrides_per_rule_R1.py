"""Phase 9 Wave 2 — per-rule unit test for R1 strip-variants.

Worked example (RESEARCH §"Per-R-class Implementation Notes" R1):
  IndexEntry(canonical="hearing", variants=["Hearings"]) +
  R1StripVariantsRule(term="hearing")
  → first apply: variants == []
  → second apply (allow_stale=True): silent no-op (variants already []).
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import EditorialOverrides, R1StripVariantsRule
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


def _make_entry(canonical: str, *, variants: list[str] | None = None) -> IndexEntry:
    return IndexEntry(
        id=_slug(canonical),
        canonical=canonical,
        sort_key=canonical.lower(),
        locators=[],
        variants=variants or [],
    )


def _fix(rules: list[R1StripVariantsRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {"metadata": _SIGNED_META, "R1_strip_variants": [r.model_dump() for r in rules]}
    )


def test_R1_happy_path_strips_variants() -> None:
    entries = [_make_entry("hearing", variants=["Hearings", "hearings"])]
    fixture = _fix([R1StripVariantsRule(term="hearing")])
    result = apply_editorial_overrides(entries, fixture)
    assert len(result.entries) == 1
    assert result.entries[0].canonical == "hearing"
    assert result.entries[0].variants == []
    assert result.mismatches == ()


def test_R1_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("hearing", variants=["Hearings"])]
    fixture = _fix([R1StripVariantsRule(term="hearing")])
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]
    assert [e.variants for e in once.entries] == [e.variants for e in twice.entries]


def test_R1_mismatch_first_apply_raises() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R1StripVariantsRule(term="hearing")])
    with pytest.raises(EditorialOverrideMismatch):
        apply_editorial_overrides(entries, fixture, allow_stale=False)


def test_R1_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix([R1StripVariantsRule(term="hearing")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert len(result.mismatches) == 1
    assert result.mismatches[0].rule_class == "R1_strip_variants"
