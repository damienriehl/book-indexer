"""Tests for Unicode canonical normalizer (D-15)."""
from __future__ import annotations

import pytest

from book_indexer.ingest.normalizer import (
    canonicalize_text,
    normalize,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ﬁrst ﬂame", "first flame"),               # U+FB01, U+FB02
        ("oﬃcial", "official"),                             # U+FB03
        ("waﬄes", "waffles"),                               # U+FB04
        ("ﬅy", "fty"),                                      # U+FB05
        ("faﬆ", "fast"),                                    # U+FB06
        ("‘quote’", "'quote'"),                        # smart singles
        ("“hello”", '"hello"'),                        # smart doubles
        ("a–b", "a-b"),                                     # en-dash
        ("a—b", "a--b"),                                    # em-dash
        ("soft­hyphen", "softhyphen"),                      # soft hyphen stripped
        ("NBSP here", "nbsp here"),                         # NBSP -> space
        ("zwsp​here", "zwsphere"),                          # ZWSP stripped
        ("bom﻿here", "bomhere"),                            # BOM stripped
        ("ellipsis…", "ellipsis..."),                       # ellipsis -> "..."
        ("  MixedCASE\t\n  ", "mixedcase"),                      # whitespace + case
    ],
)
def test_normalize_cases(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_preserves_section_sign() -> None:
    assert normalize("§ 404") == "§ 404"  # U+00A7 NOT remapped


def test_canonicalize_text_preserves_visible_chars() -> None:
    # Keeps ligatures, smart quotes, dashes; strips only useless invisible.
    s = "ﬁrst ‘quote’ — end­​"
    assert canonicalize_text(s) == "ﬁrst ‘quote’ — end"


def test_normalize_has_no_ligatures_in_output() -> None:
    s = "ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ"
    out = normalize(s)
    assert all(not (0xFB00 <= ord(c) <= 0xFB06) for c in out)


def test_normalize_output_has_no_smart_quotes_or_nbsp_or_zwsp() -> None:
    s = "‘x’ “y”   ​ ﻿ ­"
    out = normalize(s)
    for ch in out:
        cp = ord(ch)
        assert cp not in (0x2018, 0x2019, 0x201C, 0x201D)
        assert cp != 0x00A0
        assert cp != 0x200B
        assert cp != 0xFEFF
        assert cp != 0x00AD


def test_normalize_empty_string() -> None:
    assert normalize("") == ""


def test_canonicalize_text_empty_string() -> None:
    assert canonicalize_text("") == ""


def test_normalize_dash_examples() -> None:
    """D-15: en-dash -> '-', em-dash -> '--'."""
    assert normalize("a–b–c") == "a-b-c"
    assert normalize("x — y") == "x -- y"
