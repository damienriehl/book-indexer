"""Unit tests for ``book_indexer.tables.verifier_bridge``.

The verifier_bridge is a thin shim layer over Phase 2 ``verify()``. Its
sole job is to materialize the (Iterator[Evidence]) into a sorted list,
applying citation-form-specific call shapes:

* ``verify_case``    — single ``verify()`` call with the full display_name.
* ``verify_statute`` — TWO ``verify()`` calls (canonical + surface) with
                       de-dup on the (pdf_page, token_offset) pair
                       (Pitfall P-5: Sec. vs § surface form).
* ``verify_rule``    — single ``verify()`` call with the BARE PARENT only
                       (e.g., 'FRE 404', NEVER 'FRE 404(b)') — Phase 1's
                       tokenizer fuses parenthesized subsections into the
                       rule-number token (Pitfall P-2).

Architecture Lock #1: this module does NOT construct Evidence. It only
calls ``verify()`` and forwards its iterator. Verified by AST scan in
``test_no_evidence_construction``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from book_indexer.verify.evidence import Evidence

_BRIDGE_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "book_indexer" / "tables" / "verifier_bridge.py"
)


def _make_evidence(*, pdf_page: int, token_offset: int, term: str = "FRE 404") -> Evidence:
    """Build a synthetic Evidence row for mock-based tests.

    Constructed only for test fixtures (Lock #1 allows test code to
    instantiate Evidence directly — the AST scanner is scoped to
    ``src/book_indexer/`` only).
    """
    return Evidence(
        canonical_term=term,
        matched_variant=term,
        section_ref="§3.07",
        section_level=2,
        section_path=("§3", "§3.07"),
        folio="73",
        pdf_page=pdf_page,
        token_offset=token_offset,
        match_mode="exact",
        verbatim_snippet="x" * 60 + " context for the matched evidence row in tests",
    )


# --- verify_case --------------------------------------------------------


def test_verify_case_empty_returns_empty() -> None:
    from book_indexer.tables.verifier_bridge import verify_case
    conn = MagicMock()
    assert verify_case("", conn) == []
    assert verify_case("   ", conn) == []


def test_verify_case_calls_verify_once_with_full_display_name() -> None:
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    fake_ev = _make_evidence(pdf_page=42, token_offset=10, term="Jones v. Barnes")
    with patch.object(verifier_bridge, "verify", return_value=iter([fake_ev])) as mv:
        rows = verifier_bridge.verify_case("Jones v. Barnes", conn)
    mv.assert_called_once_with("Jones v. Barnes", conn)
    assert rows == [fake_ev]


def test_verify_case_returns_sorted_by_page_and_offset() -> None:
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    e1 = _make_evidence(pdf_page=50, token_offset=5)
    e2 = _make_evidence(pdf_page=42, token_offset=99)
    e3 = _make_evidence(pdf_page=42, token_offset=10)
    # Pass deliberately scrambled order; verify() in real life yields
    # already-sorted but the shim is robust to any iterator order.
    with patch.object(verifier_bridge, "verify", return_value=iter([e1, e2, e3])):
        rows = verifier_bridge.verify_case("Jones v. Barnes", conn)
    assert [(r.pdf_page, r.token_offset) for r in rows] == [(42, 10), (42, 99), (50, 5)]


# --- verify_statute -----------------------------------------------------


def test_verify_statute_empty_returns_empty() -> None:
    from book_indexer.tables.verifier_bridge import verify_statute
    conn = MagicMock()
    assert verify_statute("", "", conn) == []


def test_verify_statute_canonical_only_when_no_surface() -> None:
    """surface == canonical → verify() is called exactly ONCE."""
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    fake_ev = _make_evidence(pdf_page=10, token_offset=3, term="42 U.S.C. § 1983")
    with patch.object(verifier_bridge, "verify", return_value=iter([fake_ev])) as mv:
        rows = verifier_bridge.verify_statute(
            "42 U.S.C. § 1983", "42 U.S.C. § 1983", conn
        )
    assert mv.call_count == 1
    assert rows == [fake_ev]


def test_verify_statute_canonical_plus_surface_when_distinct() -> None:
    """surface != canonical → verify() is called TWICE (P-5 contract)."""
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    ev_can = _make_evidence(pdf_page=10, token_offset=3, term="28 U.S.C. § 1407")
    ev_sur = _make_evidence(pdf_page=22, token_offset=8, term="28 U.S.C. Sec. 1407")
    iters = iter([iter([ev_can]), iter([ev_sur])])
    with patch.object(
        verifier_bridge, "verify", side_effect=lambda *a, **kw: next(iters)
    ) as mv:
        rows = verifier_bridge.verify_statute(
            "28 U.S.C. § 1407", "28 U.S.C. Sec. 1407", conn
        )
    assert mv.call_count == 2
    assert {(r.pdf_page, r.token_offset) for r in rows} == {(10, 3), (22, 8)}


def test_verify_statute_dedup_by_pdf_page_and_offset() -> None:
    """Overlapping (pdf_page, token_offset) across canonical + surface
    must be de-duped. Same hit emerges twice if both phrasings tokenize
    to overlapping spans on the corpus.
    """
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    same = _make_evidence(pdf_page=10, token_offset=3)
    iters = iter([iter([same]), iter([same])])
    with patch.object(verifier_bridge, "verify", side_effect=lambda *a, **kw: next(iters)):
        rows = verifier_bridge.verify_statute("a", "b", conn)
    assert len(rows) == 1


def test_verify_statute_skips_canonical_when_blank() -> None:
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    ev = _make_evidence(pdf_page=5, token_offset=2)
    with patch.object(verifier_bridge, "verify", return_value=iter([ev])) as mv:
        rows = verifier_bridge.verify_statute("", "Seventh Amendment", conn)
    mv.assert_called_once_with("Seventh Amendment", conn)
    assert rows == [ev]


# --- verify_rule --------------------------------------------------------


def test_verify_rule_empty_returns_empty() -> None:
    from book_indexer.tables.verifier_bridge import verify_rule
    conn = MagicMock()
    assert verify_rule("", conn) == []
    assert verify_rule("   ", conn) == []


def test_verify_rule_bare_parent_works() -> None:
    """verify_rule('FRE 404', conn) → list of Evidence (Pitfall P-2 OK path)."""
    from book_indexer.tables import verifier_bridge
    conn = MagicMock()
    ev = _make_evidence(pdf_page=140, token_offset=5, term="FRE 404")
    with patch.object(verifier_bridge, "verify", return_value=iter([ev])) as mv:
        rows = verifier_bridge.verify_rule("FRE 404", conn)
    mv.assert_called_once_with("FRE 404", conn)
    assert rows == [ev]


def test_verify_rule_strips_parenthetical_NO_silent_pass() -> None:
    """verify_rule('FRE 404(b)', conn) raises ValueError per P-2 contract.

    Phase 1's tokenizer fuses '404(b' into one token, so passing a
    parenthetical to verify() yields 0 hits silently. The shim refuses
    the call to surface the contract violation at the boundary.
    """
    from book_indexer.tables.verifier_bridge import verify_rule
    conn = MagicMock()
    with pytest.raises(ValueError, match="parenthetical"):
        verify_rule("FRE 404(b)", conn)
    with pytest.raises(ValueError):
        verify_rule("FRE 404(b)(1)", conn)


# --- Architecture Lock #1 source-level checks ---------------------------


def test_no_evidence_construction_in_verifier_bridge() -> None:
    """Lock #1: verifier_bridge.py must NOT construct Evidence directly.

    The module may import Evidence (TYPE-only); it must never call
    ``Evidence(...)`` to construct a row. Use AST to be robust against
    docstring/comment substring false positives.
    """
    src = _BRIDGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "Evidence":
                pytest.fail(
                    f"verifier_bridge.py:{node.lineno} constructs Evidence "
                    "directly — Architecture Lock #1 violated."
                )
            if isinstance(func, ast.Attribute) and func.attr == "Evidence":
                pytest.fail(
                    f"verifier_bridge.py:{node.lineno} constructs Evidence "
                    "via attribute access — Architecture Lock #1 violated."
                )


def test_imports_verify_from_phase_2() -> None:
    """The module MUST import ``verify`` from ``book_indexer.verify.verifier``."""
    src = _BRIDGE_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"^from book_indexer\.verify\.verifier import verify\b",
        src,
        flags=re.MULTILINE,
    ), "verifier_bridge.py must import verify from book_indexer.verify.verifier"
