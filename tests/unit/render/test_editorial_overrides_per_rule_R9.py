"""Phase 9 Wave 2 — per-rule unit test for R9 whitespace.

R9 operates on rendered TEXT, not on the IR. The apply pass is an IR no-op
for R9; the renderer's post-emit hook calls ``apply_r9_whitespace(text, rules)``
on the rendered markdown / docx-run text.

The 4 standard cases here exercise both the IR no-op contract and the
text-half function.
"""
from __future__ import annotations

from book_indexer.curator.fixture import EditorialOverrides, R9WhitespaceRule
from book_indexer.render.editorial_overrides import (
    apply_editorial_overrides,
    apply_r9_whitespace,
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


def _fix(rules: list[R9WhitespaceRule]) -> EditorialOverrides:
    return EditorialOverrides.model_validate(
        {"metadata": _SIGNED_META, "R9_whitespace": [r.model_dump() for r in rules]}
    )


def test_R9_happy_path_text_half_replaces_substring() -> None:
    rules = [R9WhitespaceRule(before="client ’s", after="client's")]
    out = apply_r9_whitespace("client ’s position", rules)
    assert out == "client's position"


def test_R9_idempotence_text_half() -> None:
    rules = [R9WhitespaceRule(before="client ’s", after="client's")]
    once = apply_r9_whitespace("client ’s position", rules)
    twice = apply_r9_whitespace(once, rules)
    assert once == twice == "client's position"


def test_R9_apply_pass_is_ir_no_op() -> None:
    """The apply pass returns entries unchanged; R9 mismatch detection
    cannot fire at IR level (text-only rule).
    """
    entries = [_make_entry("witness")]
    fixture = _fix([R9WhitespaceRule(before="foo", after="bar")])
    # allow_stale=False MUST NOT raise — R9 is IR no-op, never mismatch.
    result = apply_editorial_overrides(entries, fixture, allow_stale=False)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert result.mismatches == ()


def test_R9_escape_hatch_skip_with_allow_stale() -> None:
    """allow_stale=True is identical to False for R9 — IR no-op either way."""
    entries = [_make_entry("witness")]
    fixture = _fix([R9WhitespaceRule(before="foo", after="bar")])
    result = apply_editorial_overrides(entries, fixture, allow_stale=True)
    assert [e.canonical for e in result.entries] == ["witness"]
    assert result.mismatches == ()


def test_R9_text_half_preserves_yaml_document_order() -> None:
    """Multiple rules apply in YAML document order (sequential)."""
    rules = [
        R9WhitespaceRule(before="aa", after="b"),
        R9WhitespaceRule(before="b", after="c"),
    ]
    out = apply_r9_whitespace("aa", rules)
    # First rule turns "aa" → "b", second turns "b" → "c".
    assert out == "c"
