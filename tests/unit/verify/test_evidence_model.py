"""requirements_addressed: VER-04

Unit tests for the Evidence Pydantic v2 model — the sole boundary object of
the verifier. These tests cover the schema contract; property-based coverage
of match-mode↔matched_variant consistency lives in tests/property/.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.verify import Evidence


def _valid_kwargs(**overrides):
    """Minimal valid Evidence kwargs — override individual fields to trigger
    specific validator branches."""
    base = dict(
        canonical_term="voir dire",
        matched_variant="voir dire",
        section_ref="§2.04",
        section_level=2,
        section_path=("§2", "§2.04"),
        folio="42",
        pdf_page=41,
        token_offset=12,
        match_mode="exact",
        verbatim_snippet="x" * 60,
    )
    base.update(overrides)
    return base


def test_evidence_minimal_valid_instance() -> None:
    ev = Evidence(**_valid_kwargs())
    assert ev.canonical_term == "voir dire"
    assert ev.section_path == ("§2", "§2.04")
    assert ev.match_mode == "exact"


def test_extra_forbid() -> None:
    """Lock #2 precedent: unknown fields raise extra_forbidden."""
    with pytest.raises(ValidationError) as exc:
        Evidence(**_valid_kwargs(unexpected_field="nope"))
    assert any(err["type"] == "extra_forbidden" for err in exc.value.errors())


def test_snippet_min_length() -> None:
    """verbatim_snippet must be ≥60 chars."""
    with pytest.raises(ValidationError) as exc:
        Evidence(**_valid_kwargs(verbatim_snippet="x" * 59))
    assert any(
        "at least 60 characters" in err["msg"] or err["type"] == "string_too_short"
        for err in exc.value.errors()
    )


def test_section_ref_pattern_rejects_nbsp() -> None:
    """No NBSP in section_ref per D-21 deferred note."""
    with pytest.raises(ValidationError):
        Evidence(**_valid_kwargs(section_ref="§ 2.04"))  # space, not NBSP-free


def test_section_ref_pattern_accepts_three_shapes() -> None:
    """§N, §N.NN, §N.NN.M all valid."""
    for ref, level, path in [
        ("§2",      1, ("§2",)),
        ("§2.04",   2, ("§2", "§2.04")),
        ("§2.04.1", 3, ("§2", "§2.04", "§2.04.1")),
    ]:
        ev = Evidence(**_valid_kwargs(section_ref=ref, section_level=level,
                                      section_path=path))
        assert ev.section_ref == ref


def test_section_path_length_must_equal_level() -> None:
    """D-08 cross-field invariant: len(section_path) == section_level."""
    with pytest.raises(ValidationError) as exc:
        Evidence(**_valid_kwargs(
            section_ref="§2.04.1", section_level=3,
            section_path=("§2", "§2.04"),  # length 2, level 3 → mismatch
        ))
    assert any("section_path length" in str(err.get("msg", ""))
               for err in exc.value.errors())


def test_section_path_last_must_equal_section_ref() -> None:
    """D-08 cross-field invariant: section_path[-1] == section_ref."""
    with pytest.raises(ValidationError) as exc:
        Evidence(**_valid_kwargs(
            section_ref="§2.04",
            section_path=("§2", "§2.99"),  # last != section_ref
        ))
    assert any("section_path[-1]" in str(err.get("msg", ""))
               for err in exc.value.errors())


def test_evidence_is_frozen_and_hashable() -> None:
    """frozen=True → instances are hashable (tuple section_path keeps it so)."""
    ev = Evidence(**_valid_kwargs())
    with pytest.raises(ValidationError):
        ev.folio = "99"  # frozen=True → assignment raises
    assert hash(ev) is not None
    s = {ev, ev}  # set-safe
    assert len(s) == 1


def test_match_mode_pattern() -> None:
    """match_mode in {exact, lemma, acronym}."""
    for mode in ["exact", "lemma", "acronym"]:
        Evidence(**_valid_kwargs(match_mode=mode))
    with pytest.raises(ValidationError):
        Evidence(**_valid_kwargs(match_mode="fuzzy"))
