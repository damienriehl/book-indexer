"""Unit tests for ``book_indexer.tables.cases``.

Covers eyecite ``FullCaseCitation`` extraction, D-04 reporter
canonicalization, and the Pitfall P-1 ``UnknownCitation`` filter
(264 bare-§-glyph false positives on the reference corpus v1.0).
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError

import pytest

from book_indexer.tables.cases import RawCaseHit, scan_cases

# --- Happy path -------------------------------------------------------------


def test_scan_cases_finds_jones_v_barnes() -> None:
    text = "Jones v. Barnes, 463 U.S. 745 (1983)"
    hits = scan_cases(text, pdf_page=42)
    assert len(hits) == 1
    h = hits[0]
    assert h.display_name == "Jones v. Barnes"
    assert h.canonical_citation == "463 U.S. 745"
    assert h.reporter == "U.S."
    assert h.year == 1983
    assert h.pdf_page == 42
    # Surface form is whatever eyecite saw (the cite span, not the parties).
    assert "463" in h.surface_form


@pytest.mark.parametrize(
    "text",
    [
        "Roe v. Wade, 410 US 113",
        "Roe v. Wade, 410 U.S. 113",
    ],
)
def test_scan_cases_handles_us_normalization(text: str) -> None:
    """D-04: eyecite's ``corrected_citation()`` normalizes ``US`` → ``U.S.``."""
    hits = scan_cases(text, pdf_page=1)
    assert len(hits) == 1
    assert hits[0].canonical_citation == "410 U.S. 113"


# --- Pitfall P-1 (UnknownCitation noise filter) -----------------------------


def test_scan_cases_drops_unknown_citation_section_glyph() -> None:
    """The bare ``§`` glyph eyecite emits as UnknownCitation must be dropped."""
    hits = scan_cases("see § 1.05 and § 2.03", pdf_page=1)
    assert hits == []


def test_scan_cases_drops_unknown_citation_internal_section_ref() -> None:
    """Internal section refs like ``§3.07.3.`` are dropped."""
    hits = scan_cases("see §3.07.3. and §1.05", pdf_page=1)
    assert hits == []


def test_scan_cases_drops_unknown_citation_double_section() -> None:
    """``§§`` (multiple section glyph) is also dropped."""
    hits = scan_cases("see §§ 1.05–2.03", pdf_page=1)
    assert hits == []


# --- Edge cases -------------------------------------------------------------


def test_scan_cases_empty_text() -> None:
    assert scan_cases("", pdf_page=1) == []


def test_scan_cases_no_cases_in_text() -> None:
    hits = scan_cases("the quick brown fox", pdf_page=1)
    assert hits == []


def test_scan_cases_returns_sorted_by_offset() -> None:
    text = (
        "First Jones v. Barnes, 463 U.S. 745 (1983) and "
        "second Roe v. Wade, 410 U.S. 113 (1973)"
    )
    hits = scan_cases(text, pdf_page=1)
    assert len(hits) >= 2
    offsets = [h.char_offset for h in hits]
    assert offsets == sorted(offsets)


def test_scan_cases_handles_missing_metadata() -> None:
    """A bare reporter cite with no plaintiff/defendant uses placeholders.

    eyecite emits a FullCaseCitation for ``463 U.S. 745`` with
    plaintiff=None, defendant=None; we must not silently drop it but
    instead fill the display_name with placeholder values.
    """
    hits = scan_cases("See 463 U.S. 745 (1983)", pdf_page=1)
    assert len(hits) == 1
    assert hits[0].display_name == "Unknown Plaintiff v. Unknown Defendant"
    assert hits[0].canonical_citation == "463 U.S. 745"


def test_scan_cases_year_robust_to_missing() -> None:
    """A cite without a year produces ``year=None``."""
    hits = scan_cases("Jones v. Barnes, 463 U.S. 745", pdf_page=1)
    # eyecite may or may not emit a hit without a year; if it does, year=None.
    if hits:
        assert hits[0].year is None


def test_scan_cases_pure_function() -> None:
    text = "Jones v. Barnes, 463 U.S. 745 (1983)"
    a = scan_cases(text, pdf_page=1)
    b = scan_cases(text, pdf_page=1)
    assert a == b


# --- Boundary preservation --------------------------------------------------


def test_module_does_not_call_verify() -> None:
    import book_indexer.tables.cases as mod

    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "verifier_bridge" not in node.module
            assert node.module is None or "resolver" not in node.module
        if isinstance(node, ast.Call):
            # No call to a name ``verify``.
            if isinstance(node.func, ast.Name):
                assert node.func.id != "verify"


def test_raw_case_hit_is_frozen() -> None:
    h = RawCaseHit(
        display_name="x v. y",
        canonical_citation="1 U.S. 1",
        reporter="U.S.",
        court=None,
        year=None,
        surface_form="1 U.S. 1",
        pdf_page=1,
        char_offset=0,
    )
    with pytest.raises(FrozenInstanceError):
        h.year = 1999  # type: ignore[misc]
