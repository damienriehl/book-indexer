"""D-03 (sections-only) section-range collapse.

Phase 7 / OUT-05 + CUR-03 / CONTEXT D-03 sub-rule b. Mirrors
``range_collapse.collapse_locators`` for the *sections-only* output variants
(``markdown_sections_only.py`` + ``docx_sections_only.py``). The dual
locator format ``§ N.NN (p. N)`` is replaced with ``§ N.NN`` only; adjacent
same-tier sections collapse into ``§§ N.NN–M.MM`` (with NBSP after the
``§§`` and EN DASH between values).

Adjacency rules (CONTEXT D-03 / RESEARCH §"Section-Range Collapse Algorithm"):

  - Same chapter, consecutive minor: ``2.04 + 2.05`` → ``§§ 2.04–2.05`` ✓
  - Same major, consecutive sub: ``2.04.1 + 2.04.2`` → ``§§ 2.04.1–2.04.2`` ✓
  - Skip (gap): ``2.04 + 2.06`` → ``§ 2.04, § 2.06`` (no collapse).
  - Cross-tier: ``2.04 + 2.05.1`` → no collapse (different depth).
  - Cross-chapter: ``2.04 + 3.01`` → no collapse.
  - Run of ≥3: ``2.04+2.05+2.06`` → single range ``§§ 2.04–2.06`` (NOT
    pairwise chain).
  - Char escapes: NBSP (U+00A0) after ``§§`` AND after ``§``; EN DASH
    (U+2013) between values; never ASCII space or hyphen-minus.
  - Single-section runs render as ``§ N.NN`` (singular ``§``).
  - Length-1 tuples (chapter-only) NEVER count as adjacent — that would
    cross-chapter-collapse, which is forbidden.

Pitfall §P-4: ALWAYS reference the module-level NBSP and EN_DASH
constants. Inline ASCII space breaks Word's no-break semantics.

requirements_addressed: OUT-05; contributes to CUR-03 (sections-only
render of post-curation IR).
"""
from __future__ import annotations

from collections.abc import Iterable

from .ir import FormattedLocator, Locator

__all__ = [
    "EN_DASH",
    "NBSP",
    "collapse_locators_sections_only",
]

# Pitfall §P-4: hard-coded codepoints; NEVER use ASCII space here.
NBSP: str = " "  # U+00A0 NO-BREAK SPACE
EN_DASH: str = "–"  # U+2013 EN DASH


def _parse_section(ref: str) -> tuple[int, ...]:
    """Strip leading ``§`` + whitespace; split on ``.``; parse to ints.

    Examples:
        ``"§2.04"``    → ``(2, 4)``
        ``"§ 2.04"``   → ``(2, 4)``
        ``"§2.04.1"``  → ``(2, 4, 1)``
        ``"§3"``       → ``(3,)``
    """
    stripped = ref.lstrip("§").lstrip()
    if not stripped:
        return ()
    parts = stripped.split(".")
    return tuple(int(p) for p in parts)


def _is_adjacent_same_tier(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """True iff ``a`` and ``b`` are same-tier neighbors with delta == 1.

    Rules (CONTEXT D-03 + RESEARCH §"Section-Range Collapse Algorithm"):
      - Same depth (``len(a) == len(b)``).
      - All but last component match exactly (same prefix tuple).
      - Last component differs by exactly 1 (``b[-1] == a[-1] + 1``).
      - Length-1 tuples (chapter-only, e.g. ``(3,)``) are NEVER adjacent —
        that would collapse across chapters, which is forbidden.
    """
    if len(a) != len(b):
        return False
    if len(a) < 2:
        # Chapter-only (length 1) NEVER counts as adjacent — cross-chapter.
        return False
    if a[:-1] != b[:-1]:
        return False
    return b[-1] == a[-1] + 1


def _format_one(ref_components: tuple[int, ...]) -> str:
    """Format an int-tuple like ``(2, 4)`` back to ``"2.04"`` / ``"2.04.1"``.

    Single-digit chapter components are emitted bare; minor components are
    zero-padded to 2 digits to match Phase 4 IR shape (``§2.04`` not
    ``§2.4``); sub-minor components emit bare (``§2.04.1`` not ``§2.04.01``).
    """
    if not ref_components:
        return ""
    parts: list[str] = [str(ref_components[0])]
    if len(ref_components) >= 2:
        parts.append(f"{ref_components[1]:02d}")
    parts.extend(str(c) for c in ref_components[2:])
    return ".".join(parts)


def collapse_locators_sections_only(
    locators: Iterable[Locator],
) -> list[FormattedLocator]:
    """Collapse a Locator iterable into sections-only ``FormattedLocator``s.

    Algorithm (mirrors ``range_collapse.collapse_locators`` shape; differs
    only in WHAT is rendered):

      1. Deduplicate by ``section_ref`` (sections-only output emits each
         section at most once — folio differences are projected out).
      2. Sort the unique section refs by parsed int-tuple.
      3. Walk with two pointers ``(i, j)``: extend ``j`` while
         ``_is_adjacent_same_tier(parsed[j-1], parsed[j])`` — yields the
         maximal run.
      4. Singleton run → ``"§ <ref>"``. Multi run → ``"§§ <lo>–<hi>"``.
      5. Empty input → empty list.

    Returns:
        list[FormattedLocator] — one entry per emitted token.
        ``evidence_ids`` is the empty tuple (sections-only output suppresses
        the page→evidence join — the audit ledger lives in the
        sections+pages variant + ``index_evidence.json``).
    """
    # 1. Deduplicate by section_ref — same-section locators with different
    # folios collapse to one entry in sections-only output.
    seen: set[str] = set()
    unique_refs: list[str] = []
    for loc in locators:
        ref = loc.section_ref
        if ref in seen:
            continue
        seen.add(ref)
        unique_refs.append(ref)

    if not unique_refs:
        return []

    # 2. Sort by parsed int-tuple (deterministic; cross-tier compares by
    # tuple ordering).
    parsed: list[tuple[tuple[int, ...], str]] = sorted(
        ((_parse_section(r), r) for r in unique_refs),
        key=lambda t: t[0],
    )

    out: list[FormattedLocator] = []

    i = 0
    n = len(parsed)
    while i < n:
        j = i
        while j + 1 < n and _is_adjacent_same_tier(parsed[j][0], parsed[j + 1][0]):
            j += 1
        run = parsed[i : j + 1]
        if len(run) == 1:
            ref_str = _format_one(run[0][0])
            rendered = f"§{NBSP}{ref_str}"
            out.append(
                FormattedLocator(
                    section_ref=run[0][1],
                    rendered=rendered,
                    is_range=False,
                    evidence_ids=(),
                )
            )
        else:
            lo_str = _format_one(run[0][0])
            hi_str = _format_one(run[-1][0])
            rendered = f"§§{NBSP}{lo_str}{EN_DASH}{hi_str}"
            out.append(
                FormattedLocator(
                    section_ref=run[0][1],
                    rendered=rendered,
                    is_range=True,
                    evidence_ids=(),
                )
            )
        i = j + 1

    return out
