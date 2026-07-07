"""OUT-01 ship-blocker: index.md locator format is `§\\xa0N.NN (p.\\xa0N)`.

Per CONTEXT D-07 / RESEARCH §H-7 / Pitfall §P-4: every locator in
``artifacts/render/index.md`` is rendered with U+00A0 NO-BREAK SPACE
between § and the section number AND between p./pp. and the folio
digits, U+2013 EN DASH for ranges (NEVER hyphen U+002D), and
plural ``pp.`` for ranges (singular ``p.`` for single folios).

This invariant grep-scans the committed markdown for any locator-shaped
substring and asserts it conforms to the regex. Also asserts the file
contains NO occurrence of ``&nbsp;`` (HTML escape) or U+002D hyphen
inside parenthetical locator positions (defense-in-depth).

requirements_addressed: OUT-01 (locator format).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.invariants


REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_MD_PATH = REPO_ROOT / "artifacts" / "render" / "index.md"

NBSP = " "  # U+00A0 NO-BREAK SPACE
EN_DASH = "–"  # U+2013 EN DASH

# The OUT-01 spec regex — every locator in the markdown must match.
# Matches the section anchor `§\xa0N(.NN(.M)?)?` and the parenthetical
# `(p.\xa0N)` or `(pp.\xa0N–M)` (en-dash, NOT hyphen).
_LOCATOR_RE = re.compile(
    r"§ \d+(\.\d{2}(\.\d+)?)? \(pp?\. \d+(?:–\d+)?\)"
)

# Liberal sniff: any "§" followed by digits within the file, used to
# locate locator-bearing lines for a stricter check.
_SNIFF_RE = re.compile(r"§\S*\s*\(pp?[^)]*\)")


def _skip_if_missing() -> None:
    if not INDEX_MD_PATH.is_file():
        pytest.skip(
            f"Plan 05-05 cold-build commit pending: "
            f"{INDEX_MD_PATH.relative_to(REPO_ROOT)} absent"
        )


def test_index_md_no_html_nbsp() -> None:
    """OUT-01: NEVER use ``&nbsp;`` HTML escape in markdown body.

    Pitfall §P-4: HTML entities don't preserve no-break semantics in
    DOCX/PDF round-trips. Always use U+00A0 codepoint directly.
    """
    _skip_if_missing()
    text = INDEX_MD_PATH.read_text(encoding="utf-8")
    assert "&nbsp;" not in text, (
        "OUT-01: artifacts/render/index.md contains literal '&nbsp;' "
        "HTML escape; use U+00A0 codepoint instead"
    )


def test_index_md_no_ascii_hyphen_in_ranges() -> None:
    """OUT-01: ranges use U+2013 en-dash (never U+002D ASCII hyphen).

    Scans only inside the parenthetical (pp. ...) groups so prose-level
    hyphens (e.g., 'cross-examination') are not flagged.
    """
    _skip_if_missing()
    text = INDEX_MD_PATH.read_text(encoding="utf-8")
    bad: list[str] = []
    for m in re.finditer(r"\(pp?\.[^)]*\)", text):
        body = m.group(0)
        if "-" in body and EN_DASH not in body:
            # ASCII hyphen with no en-dash → wrong range glyph
            bad.append(body)
    assert not bad, (
        f"OUT-01: ASCII hyphen U+002D found in range parenthetical(s): "
        f"{bad[:5]} (use U+2013 en-dash)"
    )


def test_every_locator_matches_canonical_regex() -> None:
    """OUT-01: every locator-shaped substring matches the canonical regex.

    Pulls each ``§...(p|pp. ...)`` substring via the liberal sniff and
    asserts it conforms to the strict regex.
    """
    _skip_if_missing()
    text = INDEX_MD_PATH.read_text(encoding="utf-8")
    nonconforming: list[str] = []
    for m in _SNIFF_RE.finditer(text):
        candidate = m.group(0)
        if not _LOCATOR_RE.fullmatch(candidate):
            nonconforming.append(candidate)
    assert not nonconforming, (
        f"OUT-01: {len(nonconforming)} locator(s) in index.md fail "
        f"canonical regex {_LOCATOR_RE.pattern!r}; first 5 = "
        f"{nonconforming[:5]!r}"
    )


def test_at_least_one_locator_present() -> None:
    """Sanity: the markdown has at least one locator (the reference corpus is non-empty)."""
    _skip_if_missing()
    text = INDEX_MD_PATH.read_text(encoding="utf-8")
    matches = list(_LOCATOR_RE.finditer(text))
    assert len(matches) >= 1, (
        "OUT-01: index.md contains no locators conforming to "
        f"{_LOCATOR_RE.pattern!r}; either the file is empty or the regex "
        f"drifted from the renderer's actual output"
    )
