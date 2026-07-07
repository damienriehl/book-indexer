"""D-03 page-range collapse.

Given a list of Locators for a single entry, collapse contiguous integer
folios within the same section_ref to a range '§ N.NN (pp. N–M)' with
U+00A0 (NBSP) between § and N.NN AND between p./pp. and the folio digits,
and U+2013 (en-dash, NOT hyphen) as the range separator.

Per RESEARCH §H-6 this is VACUOUSLY EXERCISED on the reference corpus because
Phase 4's hybrid deepest-section cite-rule already coalesces multi-folio
occurrences within a section_ref to ONE Locator. Phase 5 ships
range_collapse.py with full unit fixtures (per RESEARCH §H-6's 6 synthetic
cases) for forward-compat with companion volumes.

Pitfall §P-4: ALWAYS reference the module-level NBSP constant (never
inline ' '/U+0020). A single space between § and N would break Word's
'do not break across line' semantics.

requirements_addressed: implicit D-03 (CONTEXT-locked); contributes to
OUT-01 (markdown locator format) and OUT-02 (DOCX locator format).
"""
from __future__ import annotations

from collections.abc import Iterable

from .ir import FormattedLocator, Locator

# Pitfall §P-4: hard-coded codepoints; NEVER use a regular ASCII space here.
NBSP: str = " "   # U+00A0 NO-BREAK SPACE
EN_DASH: str = "–"  # U+2013 EN DASH


def _is_arabic(folio: str) -> bool:
    """True iff folio is a non-empty string of ASCII digits."""
    return bool(folio) and folio.isdigit()


def _format_section_ref(section_ref: str) -> str:
    """Strip leading § from Phase 4 IR shape, prepend `§` + NBSP.

    Phase 4 IR emits '§2.04' (no space). Render emits '§ 2.04' (with
    NBSP) preserving non-break semantics.
    """
    s = section_ref.lstrip("§").lstrip()
    return f"§{NBSP}{s}"


def format_single_locator(section_ref: str, folio: str) -> str:
    """'§ 2.04 (p. 78)' — single-folio render with NBSP after § and after p."""
    return f"{_format_section_ref(section_ref)} (p.{NBSP}{folio})"


def _format_range_locator(section_ref: str, low: int, high: int) -> str:
    """'§ 2.04 (pp. 78–80)' — collapsed-range render.

    Plural 'pp.' (not 'p.'); en-dash (not hyphen) per CONTEXT D-03.
    """
    return f"{_format_section_ref(section_ref)} (pp.{NBSP}{low}{EN_DASH}{high})"


def collapse_locators(locators: Iterable[Locator]) -> list[FormattedLocator]:
    """D-03 contiguous-folio collapse.

    Algorithm:
      1. Group locators by section_ref.
      2. Within each group, partition by folio_kind (Arabic vs non-Arabic).
      3. Sort Arabic folios by integer value; emit contiguous runs (>=2)
         as range FormattedLocators; emit singletons as single
         FormattedLocators.
      4. Emit non-Arabic (Roman, prefixed) folios individually (no collapse).
      5. Sort the result by (section_ref, low_folio) for byte-determinism.

    Returns: list[FormattedLocator]; one per emitted locator string.
    """
    by_section: dict[str, list[Locator]] = {}
    for loc in locators:
        by_section.setdefault(loc.section_ref, []).append(loc)

    out: list[FormattedLocator] = []

    for section_ref, locs in by_section.items():
        formatted_section = _format_section_ref(section_ref)

        arabic = sorted(
            [loc for loc in locs if _is_arabic(loc.folio)],
            key=lambda loc: int(loc.folio),
        )
        non_arabic = sorted(
            [loc for loc in locs if not _is_arabic(loc.folio)],
            key=lambda loc: loc.folio,
        )

        # Emit non-Arabic individually (no collapse possible).
        for loc in non_arabic:
            out.append(FormattedLocator(
                section_ref=formatted_section,
                rendered=format_single_locator(section_ref, loc.folio),
                is_range=False,
                evidence_ids=(loc.evidence_id,),
            ))

        # Walk Arabic, partitioning into contiguous runs.
        i = 0
        while i < len(arabic):
            j = i
            while (j + 1 < len(arabic)
                   and int(arabic[j + 1].folio) == int(arabic[j].folio) + 1):
                j += 1
            run = arabic[i:j + 1]
            if len(run) == 1:
                out.append(FormattedLocator(
                    section_ref=formatted_section,
                    rendered=format_single_locator(section_ref, run[0].folio),
                    is_range=False,
                    evidence_ids=(run[0].evidence_id,),
                ))
            else:
                low = int(run[0].folio)
                high = int(run[-1].folio)
                out.append(FormattedLocator(
                    section_ref=formatted_section,
                    rendered=_format_range_locator(section_ref, low, high),
                    is_range=True,
                    evidence_ids=tuple(loc.evidence_id for loc in run),
                ))
            i = j + 1

    # Stable sort by (section_ref, first_arabic_folio_or_string_folio).
    def section_sort_key(fl: FormattedLocator) -> tuple[str, int, str]:
        import re
        m = re.search(r"\(pp?\. ([0-9]+)", fl.rendered)
        if m:
            return (fl.section_ref, 0, str(int(m.group(1))).zfill(8))
        # Non-Arabic single-folio (e.g., Roman 'iii') — sort string after numerics
        m2 = re.search(r"\(p\. ([^)]+)\)", fl.rendered)
        return (fl.section_ref, 1, m2.group(1) if m2 else "")

    out.sort(key=section_sort_key)
    return out
