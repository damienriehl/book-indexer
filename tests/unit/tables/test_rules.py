"""Unit tests for ``book_indexer.tables.rules``.

Covers chapter-rule-systems loading, bare-``Rule N`` resolution via
chapter context (D-06), explicit-prefix-wins de-duplication, and
subsection_path capture for D-05 nesting.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from book_indexer.tables.errors import ChapterRuleSystemError
from book_indexer.tables.rules import (
    load_chapter_rule_systems,
    scan_rules_with_subsections,
)

# --- load_chapter_rule_systems ---------------------------------------------


def test_load_chapter_rule_systems_returns_dict(
    chapter_rule_systems_path: Path,
) -> None:
    mapping = load_chapter_rule_systems(chapter_rule_systems_path)
    assert isinstance(mapping, dict)
    # Wave 0 fixture has 5 chapters (1-5).
    assert set(mapping.keys()) == {1, 2, 3, 4, 5}
    # Author-confirmed values per Plan 00 sign-off.
    assert mapping[1] == "MRPC"
    assert mapping[2] == "FRCP"
    assert mapping[3] == "FRE"
    assert mapping[4] == "FRE"
    assert mapping[5] == "FRCP"


def test_load_chapter_rule_systems_default_path() -> None:
    """Calling with no path resolves to fixtures/chapter_rule_systems.yaml."""
    mapping = load_chapter_rule_systems()
    assert mapping[1] == "MRPC"


def test_load_chapter_rule_systems_pending_author_raises(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "metadata:\n"
        "  curated_by: PENDING_AUTHOR\n"
        "chapters:\n"
        "  - chapter: 1\n"
        "    rule_system: none\n",
        encoding="utf-8",
    )
    with pytest.raises(ChapterRuleSystemError):
        load_chapter_rule_systems(bad)


def test_load_chapter_rule_systems_missing_file_raises(
    tmp_path: Path,
) -> None:
    with pytest.raises(ChapterRuleSystemError):
        load_chapter_rule_systems(tmp_path / "does-not-exist.yaml")


# --- scan_rules_with_subsections: explicit-prefix path ----------------------


def test_explicit_fre_no_chapter_inference() -> None:
    hits = scan_rules_with_subsections(
        "FRE 611",
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRE"
    assert hits[0].rule_number == 611
    assert hits[0].chapter_inferred is False


def test_explicit_subsection_capture() -> None:
    hits = scan_rules_with_subsections(
        "FRE 404(b)(1)",
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    assert len(hits) == 1
    assert hits[0].subsection_path == "(b)(1)"


# --- scan_rules_with_subsections: bare-Rule path (D-06) ---------------------


def test_bare_rule_in_fre_chapter() -> None:
    """Chapter 3 (FRE) → bare 'Rule 611' resolves to FRE."""
    hits = scan_rules_with_subsections(
        "see Rule 611(a)",
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRE"
    assert hits[0].rule_number == 611
    assert hits[0].subsection_path == "(a)"
    assert hits[0].chapter_inferred is True


def test_bare_rule_in_frcp_chapter() -> None:
    hits = scan_rules_with_subsections(
        "see Rule 12(b)(6)",
        pdf_page=1,
        chapter=2,
        jurisdictions=["us"],
        chapter_rule_systems={2: "FRCP"},
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "FRCP"
    assert hits[0].rule_number == 12
    assert hits[0].subsection_path == "(b)(6)"
    assert hits[0].chapter_inferred is True


def test_bare_rule_in_none_chapter_routes_to_unspecified() -> None:
    """rule_system='none' → bare 'Rule N' routes to the 'Rule' pseudo-system."""
    hits = scan_rules_with_subsections(
        "see Rule 1",
        pdf_page=1,
        chapter=99,
        jurisdictions=["us"],
        chapter_rule_systems={99: "none"},
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "Rule"
    assert hits[0].chapter_inferred is True


def test_bare_rule_in_unmapped_chapter_routes_to_unspecified() -> None:
    """Chapter not in the mapping → treats as 'none'."""
    hits = scan_rules_with_subsections(
        "see Rule 5",
        pdf_page=1,
        chapter=42,
        jurisdictions=["us"],
        chapter_rule_systems={},  # chapter 42 not present
    )
    assert len(hits) == 1
    assert hits[0].rule_system == "Rule"


# --- De-duplication: explicit prefix wins -----------------------------------


def test_explicit_fre_does_not_double_count() -> None:
    """``FRE 404`` must NOT also be re-counted by the bare-Rule regex."""
    hits = scan_rules_with_subsections(
        "see FRE 404",
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    # Bare-Rule regex requires literal "Rule" word; "FRE 404" has no
    # "Rule" so only the explicit hit appears.
    assert len(hits) == 1
    assert hits[0].rule_system == "FRE"


def test_explicit_prefix_wins_over_bare_rule_at_same_offset() -> None:
    """``Federal Rule of Civil Procedure 12`` must not also produce a
    bare-Rule hit on the inner ``Rule 12`` word.

    The bare-Rule regex DOES match ``Rule 12`` inside the prose form,
    but the de-dup logic drops it because an explicit hit shares the
    region.
    """
    text = "see Federal Rule of Civil Procedure 12(b)(6)"
    hits = scan_rules_with_subsections(
        text,
        pdf_page=1,
        chapter=2,
        jurisdictions=["us"],
        chapter_rule_systems={2: "FRCP"},
    )
    # Should be exactly 1 hit (the prose form), not 2.
    assert len(hits) == 1
    assert hits[0].rule_system == "FRCP"
    assert hits[0].rule_number == 12
    assert hits[0].chapter_inferred is False


# --- Sort + determinism -----------------------------------------------------


def test_returns_sorted_by_offset() -> None:
    text = "Rule 1 then FRE 611 then Rule 5"
    hits = scan_rules_with_subsections(
        text,
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    offsets = [h.char_offset for h in hits]
    assert offsets == sorted(offsets)


def test_pure_function_determinism() -> None:
    text = "FRE 404(b) and Rule 611"
    a = scan_rules_with_subsections(
        text,
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    b = scan_rules_with_subsections(
        text,
        pdf_page=1,
        chapter=3,
        jurisdictions=["us"],
        chapter_rule_systems={3: "FRE"},
    )
    assert a == b


# --- Boundary preservation --------------------------------------------------


def test_module_does_not_import_eyecite_or_call_verify() -> None:
    """rules.py is regex-only; eyecite has 0 rule coverage on the reference corpus."""
    import book_indexer.tables.rules as mod

    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "eyecite" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or "eyecite" not in node.module
            assert node.module is None or "verifier_bridge" not in node.module
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "verify"
