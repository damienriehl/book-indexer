"""Unit tests for ``heading_probe_candidates`` (D-08 algorithm).

Sources: 08-PLAN Task 1, 08-RESEARCH §Pattern 2.

requirements_addressed: COV-01.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.audit.heading_probe import (
    COMMON_STOPS,
    LEAD_VERBS,
    ProbeCandidate,
    heading_probe_candidates,
)


def test_describe_special_interrogatories_yields_phrase() -> None:
    """'Describe Special Interrogatories' → strips 'describe' → 2-token phrase emitted."""
    rows = [("§3.06", "Describe Special Interrogatories")]
    cands = heading_probe_candidates(rows)
    phrases = {c.phrase for c in cands}
    assert "special interrogatories" in phrases


def test_lead_verb_stripped() -> None:
    """'Define Voir Dire' → strips 'define' → 'voir dire' emitted; 'define voir dire' is not."""
    rows = [("§1.0", "Define Voir Dire")]
    cands = heading_probe_candidates(rows)
    phrases = {c.phrase for c in cands}
    assert "voir dire" in phrases
    assert "define voir dire" not in phrases


def test_common_stop_trailing_stripped() -> None:
    """'Discuss Strategies for' → strips 'discuss' + trailing 'for' → 'strategies' (1 token, filtered)."""
    rows = [("§1.1", "Discuss Strategies for")]
    cands = heading_probe_candidates(rows)
    phrases = {c.phrase for c in cands}
    assert all("strategies for" not in p for p in phrases)
    assert "strategies" not in phrases  # n_tokens >= 2 filter applied.


def test_short_titles_skipped() -> None:
    """Single-word and empty titles are skipped (no n_tokens >= 2 phrase possible)."""
    rows = [("§A", "Why?"), ("§B", "How"), ("§C", "")]
    assert heading_probe_candidates(rows) == []


def test_pydantic_validation_minimum_n_tokens() -> None:
    """ProbeCandidate requires n_tokens >= 2 (Lock #2 spirit on probe shape)."""
    with pytest.raises(ValidationError):
        ProbeCandidate(section_ref="§1", title="X Y", phrase="z", n_tokens=1)


def test_extra_field_rejected() -> None:
    """ProbeCandidate has ``extra='forbid'`` — unknown fields ValidationError."""
    with pytest.raises(ValidationError):
        ProbeCandidate.model_validate({
            "section_ref": "§1",
            "title": "X Y",
            "phrase": "x y",
            "n_tokens": 2,
            "page": "5",  # locator-shaped extra → MUST be rejected.
        })


def test_lead_verbs_and_stops_are_frozensets() -> None:
    """LEAD_VERBS and COMMON_STOPS are immutable frozensets (Lock #5 hygiene)."""
    assert isinstance(LEAD_VERBS, frozenset)
    assert isinstance(COMMON_STOPS, frozenset)
    assert "be" in LEAD_VERBS
    assert "the" in COMMON_STOPS


def test_three_token_form_emitted_when_available() -> None:
    """Five-content-word title yields BOTH a 3-token and a 2-token candidate."""
    rows = [("§7", "Considerations of Voir Dire Strategy")]
    cands = heading_probe_candidates(rows)
    n_tokens_set = {c.n_tokens for c in cands}
    # After stripping LEAD_VERBS (none here) and trailing COMMON_STOPS (none),
    # the trailing 3 + 2 forms are both emitted.
    assert 3 in n_tokens_set
    assert 2 in n_tokens_set


def test_lowercased_output() -> None:
    """Emitted phrases are lowercased — bucket-key comparison is case-insensitive."""
    rows = [("§7", "Examine the Voir Dire Process")]
    cands = heading_probe_candidates(rows)
    for c in cands:
        assert c.phrase == c.phrase.lower()


def test_empty_input() -> None:
    """No rows → empty list (no implicit defaults)."""
    assert heading_probe_candidates([]) == []


def test_leading_articles_stripped_research_pitfall_4() -> None:
    """RESEARCH §Pitfall 4 regression: leading 'a'/'an'/'the' MUST be stripped.

    'Why Be an Advocate?' should NOT emit 'an advocate' or 'be an advocate' —
    those are exactly the over-emission failures the pitfall cites. After
    stripping LEAD_VERBS ('why', 'be') and leading COMMON_STOPS ('an'), the
    residual is 'advocate' (1 token), which the n_tokens >= 2 filter drops.
    """
    rows = [("§1.01", "Why Be an Advocate")]
    cands = heading_probe_candidates(rows)
    phrases = {c.phrase for c in cands}
    assert "an advocate" not in phrases
    assert "be an advocate" not in phrases


def test_a_case_does_not_leak() -> None:
    """'a case' (the canonical over-emission failure mode) MUST NOT survive the probe."""
    rows = [("§1", "Advocating a Case")]
    cands = heading_probe_candidates(rows)
    phrases = {c.phrase for c in cands}
    # 'a case' ALONE must not be a yielded phrase. 'advocating a case' as the
    # 3-token form is allowed (the indexer might canonicalize 'advocating a
    # case' upstream); but the bare 'a case' bigram must not be emitted.
    assert "a case" not in phrases
