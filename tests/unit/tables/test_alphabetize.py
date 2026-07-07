"""Unit tests for src/book_indexer/tables/alphabetize.py.

Locks the D-01 strip set + the 11 fixed cases from 03B-RESEARCH.md §H-4.
Strip-set extension requires a CONTEXT amendment — ``test_strip_set_size_locked``
traps silent expansion.

requirements_addressed: TAB-01.
"""
from __future__ import annotations

import pytest

from book_indexer.tables.alphabetize import STRIP_SET, sort_key


# ---------------------------------------------------------------------------
# 11 fixed D-01 cases (RESEARCH §H-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "display,expected",
    [
        ("In re Smith", "Smith"),
        ("Ex parte Jones", "Jones"),
        ("Matter of Doe", "Doe"),
        ("Estate of Garcia", "Garcia"),
        ("United States v. Gomez", "Gomez"),
        ("State v. Smith", "Smith"),
        ("People v. Smith", "Smith"),
        ("Commonwealth v. Smith", "Smith"),
        # First party stays — government is the appellee, not the plaintiff
        ("Smith v. United States", "Smith v. United States"),
        ("Smith v. State of NJ", "Smith v. State of NJ"),
        # Standard private-party case
        ("Smith v. Jones", "Smith v. Jones"),
    ],
)
def test_d01_fixed_cases(display: str, expected: str) -> None:
    assert sort_key(display) == expected


# ---------------------------------------------------------------------------
# Negative + edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "display,expected",
    [
        ("in re Smith", "Smith"),                       # case-insensitive
        ("IN RE SMITH", "SMITH"),                       # case-insensitive (display preserves case)
        ("Inreasonable Doctrine", "Inreasonable Doctrine"),  # NO false-strip — no trailing space match
        ("", ""),                                        # empty input
        ("Single", "Single"),                            # no prefix
        ("In re ", ""),                                  # bare prefix → empty (no name)
    ],
)
def test_d01_negative_and_edge_cases(display: str, expected: str) -> None:
    assert sort_key(display) == expected


# ---------------------------------------------------------------------------
# Strip-set lock — extension requires a CONTEXT amendment
# ---------------------------------------------------------------------------


def test_strip_set_size_locked() -> None:
    """D-01 strip set extension requires a CONTEXT amendment.

    If this test fails because someone added a new prefix, REJECT the
    change and require a CONTEXT amendment first.
    """
    assert len(STRIP_SET) == 8


def test_strip_set_order_locked() -> None:
    """The order is part of the contract — first-match wins, so the
    documented order in CONTEXT.md D-01 must be preserved.
    """
    assert STRIP_SET == (
        "In re ",
        "Ex parte ",
        "Matter of ",
        "Estate of ",
        "United States v. ",
        "State v. ",
        "People v. ",
        "Commonwealth v. ",
    )


def test_strip_set_is_immutable() -> None:
    """STRIP_SET is a tuple — attempting to mutate raises TypeError."""
    with pytest.raises(TypeError):
        STRIP_SET[0] = "foo"  # type: ignore[index]
