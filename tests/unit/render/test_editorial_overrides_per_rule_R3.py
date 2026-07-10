"""Phase 9 Wave 2 — per-rule unit test for R3 reword.

Worked example (CONTEXT D-01 worked example):
  IndexEntry(canonical="good evidence rule") +
  R3RewordRule(before="good evidence rule", after="best evidence rule")
  → first apply: canonical == "best evidence rule"
  → second apply (allow_stale=True): silent no-op.

R3 ambiguity case (D-03): 2+ entries sharing canonical → mismatch with
"ambiguous" suggestion.
"""
from __future__ import annotations

import pytest

from book_indexer.curator.fixture import EditorialOverrides, R3RewordRule
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


def _slug(canonical: str, suffix: int = 0) -> str:
    base = canonical.replace(" ", "-").replace("'", "").lower()
    return f"{base}-{suffix}" if suffix else base


def _make_entry(canonical: str, *, suffix: int = 0) -> IndexEntry:
    return IndexEntry(
        id=_slug(canonical, suffix),
        canonical=canonical,
        sort_key=canonical.lower(),
        locators=[],
    )


def _fix(rules: list[R3RewordRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {"metadata": _SIGNED_META, "R3_reword": [r.model_dump() for r in rules]}
    )


def test_R3_happy_path_renames_canonical() -> None:
    entries = [_make_entry("good evidence rule")]
    fixture = _fix(
        [R3RewordRule(before="good evidence rule", after="best evidence rule")]
    )
    result = apply_editorial_overrides(entries, fixture)
    assert [e.canonical for e in result.entries] == ["best evidence rule"]
    assert result.mismatches == ()


def test_R3_idempotence_under_allow_stale() -> None:
    entries = [_make_entry("good evidence rule")]
    fixture = _fix(
        [R3RewordRule(before="good evidence rule", after="best evidence rule")]
    )
    once = apply_editorial_overrides(entries, fixture, allow_stale=True)
    twice = apply_editorial_overrides(once.entries, fixture, allow_stale=True)
    assert [e.canonical for e in once.entries] == [e.canonical for e in twice.entries]


def test_R3_mismatch_first_apply_raises() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix(
        [R3RewordRule(before="good evidence rule", after="best evidence rule")]
    )
    with pytest.raises(EditorialOverrideMismatch):
        apply_editorial_overrides(entries, fixture, allow_stale=False)


def test_R3_escape_hatch_skip_with_allow_stale() -> None:
    entries = [_make_entry("witness")]
    fixture = _fix(
        [R3RewordRule(before="good evidence rule", after="best evidence rule")]
    )
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert len(result.mismatches) == 1
    assert result.mismatches[0].rule_class == "R3_reword"


def test_R3_ambiguous_two_matches_emits_ambiguous_suggestion() -> None:
    """D-03 ambiguity: 2+ entries sharing canonical → EditorialOverrideMismatch."""
    entries = [
        _make_entry("expert witnesses", suffix=1),
        _make_entry("expert witnesses", suffix=2),
    ]
    fixture = _fix(
        [R3RewordRule(before="expert witnesses", after="expert testimony")]
    )
    with pytest.raises(EditorialOverrideMismatch) as exc_info:
        apply_editorial_overrides(entries, fixture, allow_stale=False)
    assert "ambiguous" in exc_info.value.mismatches[0].suggested_action
