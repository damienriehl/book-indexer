"""Hypothesis property tests on ligature expansion + normalize() idempotence.

Complements ``tests/property/test_normalize_idempotent.py`` (the existing
Plan 02 property check) by focusing on the ING-06 ligature contract
specifically, with broader random-input coverage.

Requirements addressed:
  - ING-06 (ligature expansion is deterministic and complete)
  - ING-07 (normalize is stable under a second pass — idempotent — so the
            FTS corpus cannot drift if a caller re-normalizes inadvertently)
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from book_indexer.ingest.normalizer import normalize

LIGATURES = "ﬁﬂﬃﬄﬅﬆ"  # U+FB01..U+FB06


@settings(max_examples=400)
@given(st.text(alphabet=LIGATURES + "abc ", min_size=0, max_size=80))
def test_normalize_expands_all_ligatures(s: str) -> None:
    """requirements_addressed: ING-06

    No codepoint in U+FB00..U+FB06 may survive a single pass of normalize().
    """
    n = normalize(s)
    for ch in n:
        cp = ord(ch)
        assert not (0xFB00 <= cp <= 0xFB06), (
            f"ligature U+{cp:04X} survived normalize({s!r}) -> {n!r}"
        )


@settings(max_examples=400)
@given(st.text(min_size=0, max_size=200))
def test_normalize_is_stable_under_second_pass(s: str) -> None:
    """requirements_addressed: ING-06, ING-07

    normalize(normalize(s)) == normalize(s) for any string. Idempotence
    means the FTS-index `norm` column is safe to re-process.
    """
    once = normalize(s)
    twice = normalize(once)
    assert once == twice, (
        f"normalize not idempotent on {s!r}: once={once!r} twice={twice!r}"
    )


@settings(max_examples=200)
@given(st.text(alphabet="abcdefghijklmnop ", min_size=0, max_size=60))
def test_normalize_on_ascii_stays_ascii_lowercase(s: str) -> None:
    """requirements_addressed: ING-07

    For inputs that are already plain lowercase ASCII + spaces, normalize
    must not introduce non-ASCII codepoints or uppercase letters.
    """
    n = normalize(s)
    for ch in n:
        assert ord(ch) < 128, f"non-ASCII char {ch!r} (U+{ord(ch):04X}) introduced"
        assert ch == ch.lower(), f"uppercase {ch!r} introduced"


@settings(max_examples=300)
@given(
    st.lists(
        st.sampled_from(list(LIGATURES)),
        min_size=1,
        max_size=5,
    )
)
def test_normalize_expands_every_ligature_to_expected_ascii(chars: list[str]) -> None:
    """requirements_addressed: ING-06

    Each ligature must map to its documented ASCII expansion (D-15).
    """
    mapping = {"ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}
    s = "".join(chars)
    expected = "".join(mapping[c] for c in chars)
    assert normalize(s) == expected, (
        f"normalize({s!r}) expected {expected!r}, got {normalize(s)!r}"
    )
