"""Unit tests for ``book_indexer.tables.statutes``.

Covers eyecite ``FullLawCitation`` extraction (USC + variants), regex
Constitution fallback (Amendment + article), and jurisdiction gating
(state-statute citations dropped when ``jurisdictions=['us']``).
"""
from __future__ import annotations

import ast

import pytest

from book_indexer.tables.statutes import RawStatuteHit, scan_statutes

# --- Happy path: USC --------------------------------------------------------


def test_scan_statutes_finds_28_usc_1407() -> None:
    hits = scan_statutes(
        "28 U.S.C. § 1407",
        pdf_page=10,
        jurisdictions=["us"],
    )
    # Exactly one USC hit (and no Constitution hit since the text has none).
    usc_hits = [h for h in hits if h.title == "28"]
    assert len(usc_hits) == 1
    h = usc_hits[0]
    assert h.title == "28"
    assert h.section == "1407"
    assert h.canonical_citation == "28 U.S.C. § 1407"
    assert h.pdf_page == 10


def test_scan_statutes_handles_sec_form() -> None:
    """eyecite recognizes ``Sec.`` as a § synonym; the resulting hit
    surfaces title=28, section=1407 even though ``corrected_citation``
    preserves the ``Sec.`` form (this is eyecite 2.7.6 behavior; we
    delegate D-04 normalization to eyecite, not redefine it)."""
    hits = scan_statutes(
        "28 U.S.C. Sec. 1407",
        pdf_page=10,
        jurisdictions=["us"],
    )
    usc_hits = [h for h in hits if h.title == "28"]
    assert len(usc_hits) == 1
    h = usc_hits[0]
    assert h.title == "28"
    assert h.section == "1407"
    # canonical_citation reflects eyecite's corrected_citation() output;
    # for the Sec. surface form, eyecite 2.7.6 keeps "Sec." in the output.
    assert h.canonical_citation == "28 U.S.C. Sec. 1407"


# --- Constitution via regex fallback ----------------------------------------


def test_scan_statutes_finds_seventh_amendment() -> None:
    hits = scan_statutes(
        "the Seventh Amendment guarantees",
        pdf_page=5,
        jurisdictions=["us"],
    )
    amend_hits = [h for h in hits if h.title == "Const."]
    assert len(amend_hits) == 1
    assert amend_hits[0].display_name == "Seventh Amendment"


def test_scan_statutes_finds_us_const_art() -> None:
    hits = scan_statutes(
        "U.S. Const. art. III, § 2",
        pdf_page=5,
        jurisdictions=["us"],
    )
    art_hits = [h for h in hits if h.title == "Const."]
    assert len(art_hits) == 1
    assert art_hits[0].display_name == "U.S. Const. art. III, § 2"
    assert art_hits[0].section == "2"


# --- Jurisdiction gating ----------------------------------------------------


def test_scan_statutes_jurisdictions_excludes_state() -> None:
    """``jurisdictions=['us']`` must NOT extract state-statute citations.

    eyecite may emit a FullLawCitation for ``N.J. Rev. Stat. § 2C:11-3``;
    the statutes extractor must filter it out via the reporter check.
    """
    hits = scan_statutes(
        "see N.J. Rev. Stat. § 2C:11-3 and Cal. Penal Code § 187",
        pdf_page=1,
        jurisdictions=["us"],
    )
    # No state-statute hits: every emitted hit must be USC or Constitution.
    for h in hits:
        assert h.title == "Const." or h.title == "28" or h.title.isdigit()


def test_scan_statutes_jurisdictions_empty_yields_no_us() -> None:
    """If 'us' is NOT in jurisdictions, no USC and no Constitution emitted."""
    hits = scan_statutes(
        "28 U.S.C. § 1407 and the Seventh Amendment",
        pdf_page=1,
        jurisdictions=[],
    )
    assert hits == []


# --- Combined ordering ------------------------------------------------------


def test_scan_statutes_returns_sorted_by_offset() -> None:
    text = "28 U.S.C. § 1407 then later the Seventh Amendment"
    hits = scan_statutes(text, pdf_page=1, jurisdictions=["us"])
    assert len(hits) == 2
    offsets = [h.char_offset for h in hits]
    assert offsets == sorted(offsets)


# --- Edge cases -------------------------------------------------------------


def test_scan_statutes_empty_text() -> None:
    assert scan_statutes("", pdf_page=1, jurisdictions=["us"]) == []


def test_scan_statutes_no_statutes() -> None:
    hits = scan_statutes(
        "the quick brown fox", pdf_page=1, jurisdictions=["us"]
    )
    assert hits == []


def test_scan_statutes_pure_function() -> None:
    text = "28 U.S.C. § 1407 and the Fifth Amendment"
    a = scan_statutes(text, pdf_page=1, jurisdictions=["us"])
    b = scan_statutes(text, pdf_page=1, jurisdictions=["us"])
    assert a == b


def test_scan_statutes_uses_regex_fallback_scan_constitution() -> None:
    """Cross-module link: statutes.py must call regex_fallback.scan_constitution."""
    import book_indexer.tables.statutes as mod

    with open(mod.__file__, encoding="utf-8") as f:
        body = f.read()
    assert "scan_constitution" in body


# --- Boundary preservation --------------------------------------------------


def test_module_does_not_call_verify() -> None:
    import book_indexer.tables.statutes as mod

    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "verifier_bridge" not in node.module
            assert node.module is None or "resolver" not in node.module
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id != "verify"


def test_raw_statute_hit_is_frozen() -> None:
    h = RawStatuteHit(
        display_name="28 U.S.C. § 1407",
        canonical_citation="28 U.S.C. § 1407",
        title="28",
        section="1407",
        publisher=None,
        surface_form="28 U.S.C. § 1407",
        pdf_page=1,
        char_offset=0,
    )
    with pytest.raises(Exception):
        h.section = "999"  # type: ignore[misc]
