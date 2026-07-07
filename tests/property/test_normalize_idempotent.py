"""Property-based idempotence test for normalize() (T-01-02-01).

Proves across 500 Hypothesis examples that normalize(normalize(x)) == normalize(x)
and that none of the canonical-strip codepoints survive a single pass.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from book_indexer.ingest.normalizer import normalize


@settings(max_examples=500)
@given(st.text(min_size=0, max_size=300))
def test_normalize_is_idempotent(s: str) -> None:
    """normalize(normalize(x)) == normalize(x)"""
    once = normalize(s)
    twice = normalize(once)
    assert once == twice


@settings(max_examples=300)
@given(
    st.text(
        alphabet="fiflﬁﬂﬃﬄﬅﬆ-­‘’“”–— ​﻿ abcABC ",
        min_size=0,
        max_size=100,
    )
)
def test_normalize_strips_or_expands_all_oddities(s: str) -> None:
    n = normalize(s)
    for ch in n:
        cp = ord(ch)
        assert cp != 0x00AD, "soft hyphen must be stripped"
        assert not (0xFB00 <= cp <= 0xFB06), "ligatures must be expanded"
        assert cp not in (0x2018, 0x2019, 0x201C, 0x201D), "smart quotes must be canonical"
        assert cp != 0x00A0, "NBSP must become regular space"
        assert cp != 0x200B, "ZWSP must be stripped"
        assert cp != 0xFEFF, "BOM must be stripped"
