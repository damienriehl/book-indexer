"""Tests for B-05 surface-cruft filter (Phase 5 Wave 1).

Per CONTEXT D-04 / B-05 + RESEARCH §H-4. The drop-count calibration anchor is
empirically grounded against the reference corpus.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# --- Module-level constants and exports -----------------------------------

def test_module_exports_required_names():
    from book_indexer.render import filter as flt

    for name in (
        "is_cruft",
        "CRUFT_LEADING_CHARS",
        "OUTLINE_NUMBER_RE",
        "MIN_LEN",
        "MAX_LEN",
        "EXPECTED_DROP_COUNT",
    ):
        assert hasattr(flt, name), f"filter.py is missing export: {name}"


def test_constants_have_expected_values():
    from book_indexer.render.filter import (
        EXPECTED_DROP_COUNT,
        MAX_LEN,
        MIN_LEN,
    )

    assert MIN_LEN == 2
    assert MAX_LEN == 100
    assert EXPECTED_DROP_COUNT == 24


def test_cruft_leading_chars_includes_bullet():
    """U+2022 (bullet) must be in the leading-char set per RESEARCH §H-4."""
    from book_indexer.render.filter import CRUFT_LEADING_CHARS

    assert "•" in CRUFT_LEADING_CHARS
    assert "•" in CRUFT_LEADING_CHARS


def test_cruft_leading_chars_omits_apostrophe():
    """U+0027 (apostrophe) must NOT be in CRUFT_LEADING_CHARS — CONTEXT D-04
    explicit allow ('attorneys' fees' must survive)."""
    from book_indexer.render.filter import CRUFT_LEADING_CHARS

    assert "'" not in CRUFT_LEADING_CHARS


def test_outline_number_re_is_case_sensitive_lowercase():
    """`^[a-z]\\.\\s+` is intentionally lowercase-only; uppercase outline
    prefixes do not appear in Phase 4 IR."""
    from book_indexer.render.filter import OUTLINE_NUMBER_RE

    assert OUTLINE_NUMBER_RE.match("a. scope") is not None
    assert OUTLINE_NUMBER_RE.match("A. scope") is None


# --- Row-by-row behavior table per CONTEXT D-04 + RESEARCH §H-4 ----------

@pytest.mark.parametrize(
    "canonical,expected,reason",
    [
        # Clean canonicals — keep
        ("voir dire", False, "two-word clean canonical"),
        ("hearsay", False, "single-word clean canonical"),
        ("ab", False, "length == MIN_LEN (2) is allowed"),
        ("x" * 100, False, "length == MAX_LEN (100) is allowed"),
        ("'foo", False, "leading apostrophe (U+0027) is allowed per CONTEXT D-04"),
        ("attorneys' fees", False, "internal apostrophe survives"),
        ("A. SCOPE", False, "uppercase outline ignored — regex is case-sensitive"),
        # Length rules
        ("a", True, "length 1 < MIN_LEN"),
        ("", True, "empty string"),
        ("x" * 101, True, "length > MAX_LEN"),
        # Outline prefix
        ("a. scope", True, "outline-numbered (a.)"),
        ("d. civil", True, "outline-numbered (d.)"),
        ("i. complex", True, "outline-numbered (i.)"),
        ("i. misconduct", True, "outline-numbered (i.)"),
        # Leading char — quotes
        ('" hearsay', True, "leading dumb double-quote"),
        ('" objection', True, "leading dumb double-quote"),
        # Leading char — brackets
        ("( esi", True, "leading paren"),
        ("( fre", True, "leading paren"),
        ("[ foo", True, "leading bracket"),
        ("{ foo", True, "leading curly"),
        # Leading char — bullets
        ("• craft", True, "leading bullet (U+2022) — explicit codepoint"),
        ("• cross", True, "leading bullet (U+2022)"),
        ("• develop", True, "leading bullet (U+2022)"),
        ("• exhibit familiarity", True, "leading bullet"),
        ("• party", True, "leading bullet"),
        ("• receipt verification", True, "leading bullet"),
        # Leading char — marks
        ("# foo", True, "leading hash"),
        ("& bar", True, "leading ampersand"),
        ("@ foo", True, "leading at-sign"),
        ("* foo", True, "leading asterisk"),
        ("/ foo", True, "leading slash"),
        ("\\ foo", True, "leading backslash"),
        ("_ foo", True, "leading underscore"),
        ("` foo", True, "leading backtick"),
        ("~ foo", True, "leading tilde"),
        ("$ foo", True, "leading dollar"),
        ("% foo", True, "leading percent"),
        ("^ foo", True, "leading caret"),
        # Smart quotes
        ("“ hearsay", True, "leading smart-left double quote U+201C"),
        ("” hearsay", True, "leading smart-right double quote U+201D"),
        ("‘ hearsay", True, "leading smart-left single quote U+2018"),
        ("’ hearsay", True, "leading smart-right single quote U+2019"),
    ],
)
def test_is_cruft_row_table(canonical, expected, reason):
    from book_indexer.render.filter import is_cruft

    assert is_cruft(canonical) is expected, f"{canonical!r}: {reason}"


# --- Calibration anchor smoke against live IR ----------------------------


@pytest.mark.skipif(
    not Path("artifacts/index_tree.json").exists(),
    reason="live IR not committed yet (pre-Wave-4)",
)
def test_calibration_anchor_drops_expected():
    """RESEARCH §H-4 empirical: the expected number of entries drop on the
    reference corpus.

    This anchors against drift; the Wave 4 cold-build acceptance gate
    calibrates if the reference IR ever changes.
    """
    from book_indexer.render import IndexTree
    from book_indexer.render.filter import EXPECTED_DROP_COUNT, is_cruft

    tree = IndexTree.model_validate_json(
        Path("artifacts/index_tree.json").read_text()
    )
    drops = sum(1 for e in tree.entries if is_cruft(e.canonical))
    assert drops == EXPECTED_DROP_COUNT, (
        f"B-05 drift: expected {EXPECTED_DROP_COUNT} drops, got {drops}. "
        f"Plan 05-05 Wave 4 cold-build calibrates this anchor."
    )


@pytest.mark.skipif(
    not Path("artifacts/index_tree.json").exists(),
    reason="live IR not committed yet (pre-Wave-4)",
)
def test_calibration_dropped_entries_match_research_h4():
    """RESEARCH §H-4 names the 14 specific canonicals that should drop."""
    from book_indexer.render import IndexTree
    from book_indexer.render.filter import is_cruft

    tree = IndexTree.model_validate_json(
        Path("artifacts/index_tree.json").read_text()
    )
    dropped = sorted(e.canonical for e in tree.entries if is_cruft(e.canonical))
    expected = sorted([
        '" hearsay',
        '" objection',
        '( esi',
        '( fre',
        'a. scope',
        'd. civil',
        'i. complex',
        'i. misconduct',
        '• craft',
        '• cross',
        '• develop',
        '• exhibit familiarity',
        '• party',
        '• receipt verification',
    ])
    assert dropped == expected, (
        "B-05 drift: dropped set does not match RESEARCH §H-4 verbatim list"
    )
