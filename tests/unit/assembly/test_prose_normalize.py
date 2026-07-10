"""Unit tests for book_indexer.assembly.prose_normalize.

Covers:
- B-01 prose-form rule normalization (RESEARCH §"specifics" + §B-01 row table).
- B-02 statute-citation whitespace collapse.
- Negative cases (non-rule strings, empty strings).
- Edge cases (leading/trailing whitespace, plural/singular, subsections).
- ``PROSE_RULE_PATTERNS`` exposed as a list[(re.Pattern, str)] for reuse by
  ``dedup.py``.
"""
from __future__ import annotations

import re

import pytest

from book_indexer.assembly.prose_normalize import (
    PROSE_RULE_PATTERNS,
    collapse_whitespace,
    prose_to_canonical,
)

# ---------------------------------------------------------------------------
# B-01: prose_to_canonical — positive cases (4 RESEARCH §B-01 recovered rows
# that DID round-trip + plural/subsection variations).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        # RESEARCH §B-01 recovered rows
        ("Federal Rule of Evidence 706", "FRE 706"),
        ("Federal Rule of Civil Procedure 11", "FRCP 11"),
        ("Federal Rule of Civil Procedure 26", "FRCP 26"),
        ("Federal Rule of Evidence 901", "FRE 901"),
        ("Federal Rule of Evidence 902", "FRE 902"),
        # Plural form also matches per the regex Rule(?:s)?
        ("Federal Rules of Evidence 404", "FRE 404"),
        ("Federal Rules of Civil Procedure 12", "FRCP 12"),
        # Appellate
        ("Federal Rule of Appellate Procedure 7", "FRAP 7"),
        ("Federal Rules of Appellate Procedure 28", "FRAP 28"),
        # Model Rules of Professional Conduct
        ("Model Rule 3.3", "MRPC 3.3"),
        ("Model Rules of Professional Conduct 1.1", "MRPC 1.1"),
        ("Model Rule 8.4(a)", "MRPC 8.4(a)"),
        # Subsections preserved
        ("Federal Rule of Evidence 404(b)", "FRE 404(b)"),
        ("Federal Rule of Evidence 803(8)", "FRE 803(8)"),
        ("Federal Rule of Civil Procedure 26(b)(1)", "FRCP 26(b)(1)"),
        # Leading/trailing whitespace tolerated
        (" Federal Rule of Evidence 401 ", "FRE 401"),
        ("\tFederal Rule of Evidence 803\n", "FRE 803"),
    ],
)
def test_prose_to_canonical_positive(surface: str, expected: str) -> None:
    assert prose_to_canonical(surface) == expected


# ---------------------------------------------------------------------------
# B-01: prose_to_canonical — negative + defensive cases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface",
    [
        "voir dire",                       # not a rule reference
        "hearsay",                         # not a rule reference
        "Strickland v. Washington",        # case name
        "FRE 401",                         # already canonical — not a prose form
        "Rule 11",                         # bare rule (no "Federal" prefix)
        "",                                # empty
        "   ",                             # whitespace only
    ],
)
def test_prose_to_canonical_negative(surface: str) -> None:
    assert prose_to_canonical(surface) is None


def test_prose_to_canonical_handles_none_like_empty() -> None:
    """Empty string returns None defensively (None itself is a TypeError per
    the type signature, but empty/whitespace-only is a no-op)."""
    assert prose_to_canonical("") is None


# ---------------------------------------------------------------------------
# B-02: collapse_whitespace.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("28 U.S.C. Sec. \n1407", "28 U.S.C. Sec. 1407"),
        ("28 U.S.C. Sec.   1407", "28 U.S.C. Sec. 1407"),
        ("\t28 U.S.C. § 1983\n", "28 U.S.C. § 1983"),
        ("voir dire", "voir dire"),
        ("28\tU.S.C.\nSec.\n1407", "28 U.S.C. Sec. 1407"),
        ("  multiple   internal    spaces  ", "multiple internal spaces"),
        ("", ""),
    ],
)
def test_collapse_whitespace(inp: str, expected: str) -> None:
    assert collapse_whitespace(inp) == expected


# ---------------------------------------------------------------------------
# PROSE_RULE_PATTERNS — exposed-tuple shape contract.
# ---------------------------------------------------------------------------


def test_prose_rule_patterns_is_list_of_tuples() -> None:
    assert isinstance(PROSE_RULE_PATTERNS, list)
    assert len(PROSE_RULE_PATTERNS) >= 4
    for entry in PROSE_RULE_PATTERNS:
        assert isinstance(entry, tuple)
        assert len(entry) == 2
        pat, sys_code = entry
        assert isinstance(pat, re.Pattern)
        assert sys_code in {"FRE", "FRCP", "FRAP", "MRPC"}


def test_prose_rule_patterns_cover_four_systems() -> None:
    """All four rule systems (FRE, FRCP, FRAP, MRPC) have at least one pattern."""
    sys_codes = {sys_code for _, sys_code in PROSE_RULE_PATTERNS}
    assert sys_codes == {"FRE", "FRCP", "FRAP", "MRPC"}
