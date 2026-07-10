"""Unit tests for book_indexer.assembly.canonical.

requirements_addressed: ASM-01 (D-01 canonical-form selection — longest
spelled-out form wins; acronyms last; alphabetical tiebreak final).

Covers D-01 strict-tiebreaker order:
1. Longest spelled-out > acronym (acronym selected only if no spelled-out exists).
2. Lowest section_ref at first appearance.
3. Lowest pdf_page_ordinal within section.
4. Lowest token_index within page.
Plus deterministic alphabetical fallback when surface_provenance is empty.
"""
from __future__ import annotations

import pytest

from book_indexer.assembly.canonical import (
    elect_canonical,
    is_valid_spelled_out,
    strip_leading_article,
)
from book_indexer.assembly.dedup import BucketCandidate, SurfaceProvenance

# ---------------------------------------------------------------------------
# strip_leading_article — pure-string helper, no spaCy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("the hearsay rule", "hearsay rule"),
        ("an evidence", "evidence"),
        ("a rule", "rule"),
        ("rule of evidence", "rule of evidence"),  # no article — unchanged
        ("Federal Rules of Evidence", "Federal Rules of Evidence"),  # no leading article
        ("The Hearsay Rule", "Hearsay Rule"),  # case-insensitive head detect
        ("a", "a"),  # single-word; defensive — unchanged
        ("the", "the"),  # single-word; unchanged
        ("", ""),  # empty
    ],
)
def test_strip_leading_article(inp: str, expected: str) -> None:
    assert strip_leading_article(inp) == expected


# ---------------------------------------------------------------------------
# is_valid_spelled_out — requires spaCy.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nlp():
    import spacy

    return spacy.load("en_core_web_lg")


@pytest.mark.slow
def test_is_valid_spelled_out_matches_lemma_key(nlp) -> None:
    # "Federal Rules of Evidence" lemmatizes to "federal rule of evidence".
    assert is_valid_spelled_out("Federal Rules of Evidence", "federal rule of evidence", nlp)


@pytest.mark.slow
def test_is_valid_spelled_out_rejects_unrelated(nlp) -> None:
    assert not is_valid_spelled_out("xyzzy", "federal rule of evidence", nlp)


# ---------------------------------------------------------------------------
# elect_canonical — D-01 step 1 (longest spelled-out > acronym).
# ---------------------------------------------------------------------------


def test_elect_canonical_prefers_spelled_out_over_acronym() -> None:
    bucket = BucketCandidate(
        lemma_key="federal rule of evidence",
        surfaces=["FRE", "Federal Rules of Evidence", "Fed. R. Evid."],
    )
    assert elect_canonical(bucket) == "Federal Rules of Evidence"


def test_elect_canonical_picks_longest_spelled_out() -> None:
    bucket = BucketCandidate(
        lemma_key="rule of evidence",
        surfaces=["rule of evidence", "evidence rule", "the rules of evidence"],
    )
    # Article-stripped lengths:
    #   "rule of evidence"      → 16
    #   "evidence rule"         → 13
    #   "the rules of evidence" → 17 (article "the" stripped, "rules of evidence")
    # Longest wins → "the rules of evidence" returned verbatim.
    assert elect_canonical(bucket) == "the rules of evidence"


def test_elect_canonical_alphabetical_among_equal_length_spelled_out() -> None:
    """Two spelled-out forms tied on article-stripped length tiebreak alphabetically
    when surface_provenance is empty."""
    bucket = BucketCandidate(
        lemma_key="rule of evidence",
        # Both 16 chars after article-strip ("rule of evidence" / "the rule of...").
        surfaces=["the rule of evidence", "rule of evidence"],
    )
    # Both 16 chars after article-strip; alphabetical: "rule of evidence" <
    # "the rule of evidence" (r < t).
    assert elect_canonical(bucket) == "rule of evidence"


def test_elect_canonical_only_acronyms_returns_longest_acronym() -> None:
    bucket = BucketCandidate(
        lemma_key="fre",
        surfaces=["FRE", "FBI"],
    )
    # Both are 3-char acronyms — alphabetical fallthrough.
    chosen = elect_canonical(bucket)
    assert chosen in {"FRE", "FBI"}
    # Determinism: "FBI" alphabetically precedes "FRE".
    assert chosen == "FBI"


# ---------------------------------------------------------------------------
# elect_canonical — D-01 step 2-4 (provenance tiebreakers).
# ---------------------------------------------------------------------------


def test_elect_canonical_section_ref_tiebreaker() -> None:
    """Two equal-length forms; lower section_ref wins."""
    bucket = BucketCandidate(
        lemma_key="voir dire",
        surfaces=["voir-dire", "voir dire"],
        surface_provenance={
            "voir-dire": SurfaceProvenance(section_ref="§3.05", pdf_page=120, token_index=5),
            "voir dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=12),
        },
    )
    # Both forms are 9 chars after article-strip. §2.04 < §3.05 → "voir dire".
    assert elect_canonical(bucket) == "voir dire"


def test_elect_canonical_pdf_page_tiebreaker() -> None:
    """Same section_ref; lower pdf_page wins."""
    bucket = BucketCandidate(
        lemma_key="voir dire",
        surfaces=["voir-dire", "voir dire"],
        surface_provenance={
            "voir-dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=20),
            "voir dire": SurfaceProvenance(section_ref="§2.04", pdf_page=80, token_index=5),
        },
    )
    # Same section, page 78 < page 80 → "voir-dire".
    assert elect_canonical(bucket) == "voir-dire"


def test_elect_canonical_token_index_tiebreaker() -> None:
    """Same section + page; lower token_index wins."""
    bucket = BucketCandidate(
        lemma_key="voir dire",
        surfaces=["voir-dire", "voir dire"],
        surface_provenance={
            "voir-dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=12),
            "voir dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=5),
        },
    )
    assert elect_canonical(bucket) == "voir dire"


def test_elect_canonical_alphabetical_fallthrough_no_provenance() -> None:
    """When surface_provenance is empty, equal-length surfaces sort alphabetically."""
    bucket = BucketCandidate(
        lemma_key="voir dire",
        surfaces=["voir-dire", "voir dire"],
    )
    # Both forms are 9 chars after article-strip. surface_provenance empty →
    # alphabetical: "voir dire" < "voir-dire" (space ASCII 0x20 < hyphen 0x2D).
    assert elect_canonical(bucket) == "voir dire"


# ---------------------------------------------------------------------------
# elect_canonical — leading article stripping in length comparison.
# ---------------------------------------------------------------------------


def test_elect_canonical_strips_leading_article_in_length_comparison() -> None:
    """'the hearsay rule' (16 chars - 4 article = 12) vs 'hearsay rule' (12)
    are equal-length after article strip → tiebreak by alphabetical."""
    bucket = BucketCandidate(
        lemma_key="hearsay rule",
        surfaces=["the hearsay rule", "hearsay rule"],
    )
    chosen = elect_canonical(bucket)
    # Both are 12-char after article-strip; alphabetical: "hearsay rule"
    # before "the hearsay rule" (h < t).
    assert chosen == "hearsay rule"


# ---------------------------------------------------------------------------
# elect_canonical — defensive cases.
# ---------------------------------------------------------------------------


def test_elect_canonical_empty_surfaces_raises() -> None:
    bucket = BucketCandidate(lemma_key="foo")
    with pytest.raises(ValueError):
        elect_canonical(bucket)


def test_elect_canonical_single_surface_returns_it() -> None:
    bucket = BucketCandidate(lemma_key="voir dire", surfaces=["voir dire"])
    assert elect_canonical(bucket) == "voir dire"


# ---------------------------------------------------------------------------
# Determinism (P-1 prevention): identical bucket → identical result.
# ---------------------------------------------------------------------------


def test_elect_canonical_is_deterministic_repeated_calls() -> None:
    bucket = BucketCandidate(
        lemma_key="voir dire",
        surfaces=["voir-dire", "voir dire", "Voir Dire"],
        surface_provenance={
            "voir-dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=12),
            "voir dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=5),
            "Voir Dire": SurfaceProvenance(section_ref="§2.04", pdf_page=78, token_index=8),
        },
    )
    chosen = elect_canonical(bucket)
    for _ in range(10):
        assert elect_canonical(bucket) == chosen


def test_elect_canonical_preserves_original_casing_and_hyphenation() -> None:
    """The chosen surface is returned VERBATIM — not lowercased or altered."""
    bucket = BucketCandidate(
        lemma_key="federal rule of evidence",
        surfaces=["FRE", "Federal Rules of Evidence"],
    )
    chosen = elect_canonical(bucket)
    assert chosen == "Federal Rules of Evidence"  # original casing preserved


# ---------------------------------------------------------------------------
# Mixed acronym/spelled-out: spelled-out wins regardless of length quirks.
# ---------------------------------------------------------------------------


def test_elect_canonical_short_spelled_out_beats_acronym() -> None:
    """Even a SHORT spelled-out form wins over an acronym (D-01 step 1)."""
    bucket = BucketCandidate(
        lemma_key="motion in limine",
        surfaces=["MIL", "motion in limine"],
    )
    assert elect_canonical(bucket) == "motion in limine"
