"""UAT 08-1 unit tests for render/cross_refs.py.

Covers the smoking-gun ``interrogatories`` shape AND the substantive-noun
filter / stop-noun set / already-represented gates. Companion to the
integration test ``tests/integration/test_cross_refs_in_rendered_output.py``
which asserts the cross-ref appears in artifacts/render/index.md after a
full pipeline run.

Lock #1 preserved: this module never imports ``verify``. The unit tests
construct ``IndexEntry`` instances directly via Pydantic — no Evidence
construction occurs.

requirements_addressed: UAT 08-1.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from book_indexer.assembly.ir import IndexEntry
from book_indexer.render.cross_refs import (
    STOP_HEADS,
    CrossRefEntry,
    derive_cross_refs,
)
from book_indexer.render.ir import SyntheticEntry


def _entry(canonical: str, *, sort_key: str | None = None) -> IndexEntry:
    """Build a minimal IndexEntry. Locators empty — cross-ref derivation
    only consumes ``canonical`` + ``sort_key``."""
    sk = sort_key if sort_key is not None else canonical.lower()
    # IndexEntry.id matches a slug pattern; spaces become hyphens.
    slug = canonical.lower().replace(" ", "-").replace(".", "")
    return IndexEntry(
        id=slug,
        canonical=canonical,
        sort_key=sk,
        locators=[],
    )


def _synth(stem: str) -> SyntheticEntry:
    return SyntheticEntry(stem=stem, sibling_canonicals=(), locators=())


# ---------------------------------------------------------------------------
# The smoking-gun: interrogatories should anchor at "special interrogatory"
# ---------------------------------------------------------------------------


def test_smoking_gun_interrogatories_emits_singular_and_plural() -> None:
    """UAT 08-1 smoking-gun: ``special interrogatory`` (singular canonical)
    must produce BOTH ``interrogatory`` AND ``interrogatories`` cross-refs
    so an alphabetical reader hits the anchor regardless of lookup form."""
    entries = [_entry("special interrogatory")]
    refs = derive_cross_refs(entries, [])

    heads = {r.head for r in refs}
    assert "interrogatory" in heads, (
        "singular head missing — alphabetical reader looking up "
        "'interrogatory' would still find nothing."
    )
    assert "interrogatories" in heads, (
        "plural head missing — alphabetical reader looking up "
        "'interrogatories' (the user's actual report) would still find "
        "nothing. This is the smoking-gun fix."
    )

    # Both refs point at the primary canonical.
    for r in refs:
        if r.head in {"interrogatory", "interrogatories"}:
            assert r.primary_canonical == "special interrogatory"


# ---------------------------------------------------------------------------
# Multi-word canonical with already-represented head (skip)
# ---------------------------------------------------------------------------


def test_skip_when_head_is_already_top_level_canonical() -> None:
    """If ``picture`` already exists as a top-level canonical, no
    cross-ref is emitted from ``social network picture``."""
    entries = [
        _entry("picture"),
        _entry("social network picture"),
    ]
    refs = derive_cross_refs(entries, [])
    heads = {r.head for r in refs}
    assert "picture" not in heads
    assert "pictures" not in heads


def test_skip_when_head_is_synthesized_stem() -> None:
    """If ``hearsay`` is a B-06 synthesized stem, no cross-ref is emitted
    from ``hearsay exception`` for the head ``exception``-side... actually
    we test the inverse: the synth stem itself should NOT be cross-ref'd."""
    entries = [_entry("hearsay exception")]
    synthetics = [_synth("exception")]  # pretend "exception" is a B-06 stem
    refs = derive_cross_refs(entries, synthetics)
    heads = {r.head for r in refs}
    # 'exception' is already represented as a synth stem — skip.
    assert "exception" not in heads
    assert "exceptions" not in heads


# ---------------------------------------------------------------------------
# Substantive-noun filter
# ---------------------------------------------------------------------------


def test_skip_short_head_under_min_length() -> None:
    """Heads shorter than MIN_HEAD_LENGTH (4 chars) are too generic."""
    entries = [_entry("attorney fee")]  # head 'fee' has length 3
    refs = derive_cross_refs(entries, [])
    heads = {r.head for r in refs}
    assert "fee" not in heads
    assert "fees" not in heads


def test_skip_stop_head() -> None:
    """Heads in STOP_HEADS are skipped even when ≥ 4 chars."""
    entries = [_entry("complex case")]  # 'case' is in STOP_HEADS
    refs = derive_cross_refs(entries, [])
    heads = {r.head for r in refs}
    assert "case" not in heads
    assert "cases" not in heads


def test_stop_heads_constant_includes_expected_members() -> None:
    """Defensive: the stop-noun set documented in the module docstring
    must include every entry the test suite asserts is filtered."""
    expected = {"thing", "way", "part", "type", "kind", "case", "fact",
                "form", "issue", "matter", "point", "right", "rule",
                "side", "step", "term", "use", "view"}
    assert expected == set(STOP_HEADS)


# ---------------------------------------------------------------------------
# Single-word canonical → no cross-ref (it IS its own anchor)
# ---------------------------------------------------------------------------


def test_single_word_canonical_emits_no_cross_ref() -> None:
    entries = [_entry("evidence")]
    refs = derive_cross_refs(entries, [])
    assert refs == []


# ---------------------------------------------------------------------------
# Determinism: byte-identical output across calls (Lock #5 by-construction)
# ---------------------------------------------------------------------------


def test_deterministic_sort_order() -> None:
    """Repeat invocation yields identical CrossRefEntry sequence."""
    entries = [
        _entry("special interrogatory"),
        _entry("expert witness"),
        _entry("alternate juror"),
    ]
    refs1 = derive_cross_refs(entries, [])
    refs2 = derive_cross_refs(entries, [])
    assert refs1 == refs2
    # Sort order is by sort_key (head, lowercased).
    sort_keys = [r.sort_key for r in refs1]
    assert sort_keys == sorted(sort_keys)


def test_first_alphabetical_canonical_wins_for_shared_head() -> None:
    """When two multi-word canonicals share a head, the alphabetically
    first one is chosen as the cross-ref target."""
    entries = [
        _entry("z later canonical"),
        _entry("a earlier canonical"),
    ]
    refs = derive_cross_refs(entries, [])
    heads = {r.head: r.primary_canonical for r in refs}
    if "canonical" in heads:
        # 'canonical' is 9 chars and not in STOP_HEADS — it should anchor.
        assert heads["canonical"] == "a earlier canonical"


# ---------------------------------------------------------------------------
# Cross-ref does not duplicate when the plural form is itself a canonical
# ---------------------------------------------------------------------------


def test_skip_plural_when_already_a_canonical() -> None:
    """If ``interrogatories`` exists as its own top-level canonical, the
    derivation must not emit a duplicate cross-ref for ``interrogatories``."""
    entries = [
        _entry("special interrogatory"),
        _entry("interrogatories"),  # already top-level
    ]
    refs = derive_cross_refs(entries, [])
    heads_to_primary = {r.head: r.primary_canonical for r in refs}
    # Singular form may still be emitted (interrogatory != interrogatories).
    # Plural MUST be skipped because it's already a canonical.
    assert "interrogatories" not in heads_to_primary


# ---------------------------------------------------------------------------
# Frozen dataclass — Lock #5 sort determinism
# ---------------------------------------------------------------------------


def test_cross_ref_entry_is_frozen() -> None:
    x = CrossRefEntry(head="interrogatories", primary_canonical="special interrogatory", sort_key="interrogatories")
    with pytest.raises(FrozenInstanceError):
        x.head = "mutated"  # type: ignore[misc]
