"""Unit tests for ``book_indexer.tables.regex_fallback``.

Covers D-10 abbreviated/prose/Fed.R. patterns, MRPC pattern (Plan 00 author
sign-off), constitutional regex (RESEARCH §H-10), and jurisdiction gating.
"""
from __future__ import annotations

import re

import pytest

from book_indexer.tables.regex_fallback import (
    AMENDMENT_PATTERN,
    FEDR_PATTERN,
    FRAP_PATTERN,
    FRCP_PATTERN,
    FRE_PATTERN,
    MRPC_PATTERN,
    PROSE_RULE_PATTERN,
    RawRuleHit,
    US_CONST_ART_PATTERN,
    scan_constitution,
    scan_rules,
)

# --- D-10 abbreviated patterns ----------------------------------------------


def test_fre_pattern_matches_simple() -> None:
    hits = scan_rules("see FRE 611", jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    h = hits[0]
    assert h.rule_system == "FRE"
    assert h.rule_number == 611
    assert h.subsection_path == ""
    assert h.chapter_inferred is False
    assert h.surface_form == "FRE 611"


def test_fre_pattern_matches_subsection() -> None:
    hits = scan_rules("FRE 404(b)", jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_number == 404
    assert hits[0].subsection_path == "(b)"


def test_fre_pattern_matches_nested_subsection() -> None:
    hits = scan_rules("FRE 404(b)(1)", jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_number == 404
    assert hits[0].subsection_path == "(b)(1)"


def test_fre_pattern_does_not_match_lowercase() -> None:
    hits = scan_rules("see fre 611", jurisdictions=["us"], pdf_page=1)
    assert hits == []


@pytest.mark.parametrize(
    ("text", "expected_system", "expected_num"),
    [
        ("see FRCP 12(b)(6)", "FRCP", 12),
        ("see FRAP 4(a)", "FRAP", 4),
    ],
)
def test_frcp_and_frap_patterns_work(
    text: str, expected_system: str, expected_num: int
) -> None:
    hits = scan_rules(text, jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_system == expected_system
    assert hits[0].rule_number == expected_num


# --- D-10 Fed.R.*.P. forms ---------------------------------------------------


def test_fedr_pattern_civ_to_frcp() -> None:
    hits = scan_rules(
        "Fed. R. Civ. P. 12(b)(6)", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRCP"
    assert hits[0].rule_number == 12
    assert hits[0].subsection_path == "(b)(6)"


def test_fedr_pattern_evid_to_fre() -> None:
    hits = scan_rules(
        "Fed.R.Evid. P. 404(b)", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRE"
    assert hits[0].rule_number == 404


def test_fedr_pattern_app_to_frap() -> None:
    hits = scan_rules(
        "Fed. R. App. P. 4", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRAP"
    assert hits[0].rule_number == 4


# --- D-10 prose forms --------------------------------------------------------


def test_prose_pattern_civil_procedure() -> None:
    hits = scan_rules(
        "Federal Rule of Civil Procedure 11", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRCP"
    assert hits[0].rule_number == 11


def test_prose_pattern_evidence_plural() -> None:
    hits = scan_rules(
        "Federal Rules of Evidence 902", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRE"
    assert hits[0].rule_number == 902


def test_prose_pattern_appellate_procedure() -> None:
    hits = scan_rules(
        "Federal Rules of Appellate Procedure 4(a)",
        jurisdictions=["us"],
        pdf_page=1,
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRAP"
    assert hits[0].rule_number == 4


def test_prose_pattern_handles_intervening_whitespace() -> None:
    """Author manuscripts often re-flow whitespace across line breaks."""
    text = "see Federal Rule  of Civil Procedure 38(b)"
    hits = scan_rules(text, jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_system == "FRCP"
    assert hits[0].rule_number == 38


# --- MRPC (Plan 00 author sign-off) ------------------------------------------


def test_mrpc_pattern_full_form() -> None:
    hits = scan_rules(
        "Model Rule 3.3(a)(3)", jurisdictions=["us"], pdf_page=1
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "MRPC"
    assert hits[0].rule_number == 3
    # Subsection path captures the dotted-number plus parens.
    assert hits[0].subsection_path == ".3(a)(3)"


def test_mrpc_pattern_abbrev() -> None:
    hits = scan_rules("MRPC 1.6", jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_system == "MRPC"
    assert hits[0].rule_number == 1
    assert hits[0].subsection_path == ".6"


def test_mrpc_pattern_dotted() -> None:
    hits = scan_rules("M.R.P.C. 8.4(c)", jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 1
    assert hits[0].rule_system == "MRPC"
    assert hits[0].rule_number == 8


# --- Sort order + jurisdiction gate -----------------------------------------


def test_scan_rules_returns_sorted_by_offset() -> None:
    text = "FRAP 4 then FRCP 12 then FRE 611"
    hits = scan_rules(text, jurisdictions=["us"], pdf_page=1)
    assert len(hits) == 3
    offsets = [h.char_offset for h in hits]
    assert offsets == sorted(offsets)
    assert [h.rule_system for h in hits] == ["FRAP", "FRCP", "FRE"]


def test_scan_rules_jurisdictions_us_only_no_state() -> None:
    """jurisdictions=['us'] must NOT fire state-rule patterns."""
    hits = scan_rules(
        "see N.J.R.E. 401 and Cal. R. Civ. P. 425.13",
        jurisdictions=["us"],
        pdf_page=1,
    )
    assert hits == []


def test_scan_rules_pure_function() -> None:
    text = "FRE 611 and FRCP 12"
    a = scan_rules(text, jurisdictions=["us"], pdf_page=42)
    b = scan_rules(text, jurisdictions=["us"], pdf_page=42)
    assert a == b


# --- Constitution patterns ---------------------------------------------------


def test_amendment_pattern_seventh() -> None:
    hits = scan_constitution("the Seventh Amendment guarantees", pdf_page=5)
    assert len(hits) == 1
    assert hits[0]["kind"] == "amendment"
    assert hits[0]["display_name"] == "Seventh Amendment"


def test_amendment_pattern_handles_all_27() -> None:
    text = (
        "First Amendment, Twenty-seventh Amendment, "
        "Fourteenth Amendment, Twenty-fifth Amendment"
    )
    hits = scan_constitution(text, pdf_page=1)
    assert len(hits) == 4
    names = [h["display_name"] for h in hits]
    assert "Twenty-seventh Amendment" in names
    assert "Twenty-fifth Amendment" in names


def test_us_const_art_pattern_with_section() -> None:
    hits = scan_constitution("U.S. Const. art. III, § 2", pdf_page=10)
    assert len(hits) == 1
    h = hits[0]
    assert h["kind"] == "article"
    assert h["display_name"] == "U.S. Const. art. III, § 2"


def test_us_const_art_pattern_no_section() -> None:
    hits = scan_constitution("U.S. Const. art. III", pdf_page=10)
    assert len(hits) == 1
    assert hits[0]["display_name"] == "U.S. Const. art. III"


def test_us_const_art_pattern_no_dots() -> None:
    hits = scan_constitution("US Const art VI", pdf_page=10)
    assert len(hits) == 1
    assert hits[0]["kind"] == "article"


def test_scan_constitution_amendment_and_article_sorted() -> None:
    text = "U.S. Const. art. I, § 8 ... see also the Fifth Amendment"
    hits = scan_constitution(text, pdf_page=1)
    assert len(hits) == 2
    offsets = [h["char_offset"] for h in hits]
    assert offsets == sorted(offsets)
    assert hits[0]["kind"] == "article"
    assert hits[1]["kind"] == "amendment"


# --- Pattern compilation invariants -----------------------------------------


def test_all_patterns_are_compiled_at_import() -> None:
    """Every pattern is a re.Pattern compiled at import time (Task 1
    acceptance criterion: no lazy compilation)."""
    for pat in (
        FRE_PATTERN,
        FRCP_PATTERN,
        FRAP_PATTERN,
        FEDR_PATTERN,
        PROSE_RULE_PATTERN,
        MRPC_PATTERN,
        AMENDMENT_PATTERN,
        US_CONST_ART_PATTERN,
    ):
        assert isinstance(pat, re.Pattern)


def test_module_does_not_import_verify_or_eyecite() -> None:
    """Boundary preserved: regex_fallback is pure regex.

    Parses the module AST and asserts no Import/ImportFrom node names
    eyecite or anything from ``book_indexer.tables.verifier_bridge``
    or ``book_indexer.tables.resolver`` (Plan 03's modules).
    """
    import ast
    import book_indexer.tables.regex_fallback as mod

    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    forbidden = {"eyecite", "verifier_bridge", "resolver"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    f in alias.name for f in forbidden
                ), f"forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not any(
                f in node.module for f in forbidden
            ), f"forbidden from-import: {node.module}"


def test_raw_rule_hit_is_frozen_dataclass() -> None:
    """RawRuleHit must be frozen (immutable)."""
    h = RawRuleHit(
        rule_system="FRE",
        rule_number=611,
        subsection_path="",
        surface_form="FRE 611",
        pdf_page=1,
        char_offset=0,
        chapter_inferred=False,
    )
    with pytest.raises(Exception):
        h.rule_number = 999  # type: ignore[misc]
