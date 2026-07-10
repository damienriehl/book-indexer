"""B-05 surface-cruft filter + Phase 7 curator-pass removal helpers.

Per CONTEXT D-04 / B-05 + RESEARCH §H-4. The B-05 algorithm is empirically
grounded against the reference corpus: exactly 14 of 901 entries drop (1.55%);
Plan 05-05 cold-build acceptance gate calibrates if drift.

Phase 4's IR is preserved untouched (kept comprehensive for audit). Phase 5
drops at render time only — markdown.py / docx.py / coverage.py call
is_cruft() to skip these entries, and coverage.py logs them in the
'Dropped at render' section.

Phase 7 Wave 3 (CUR-01) extends this module with:
  - ``is_removed`` re-export from ``book_indexer.curator`` (single import
    boundary for renderers).
  - ``auto_strip_xref(text, removal_set)`` — render-time cleanup that strips
    ``See <removed>`` / ``See also <removed>`` / ``(also: ...)`` references
    pointing at curator-removed canonicals (CONTEXT D-04 dangling-xref rule).
    Pure string transform on rendered output; never mutates IR.

requirements_addressed: implicit D-04 / B-05 (CONTEXT-locked render-time
drop); CUR-01 (removal predicate re-export + dangling-xref auto-clean).
"""
from __future__ import annotations

import re

# Re-export the CUR-01 removal predicate at the render-layer module
# boundary so renderers import a single helper module
# (``book_indexer.render.filter``) for both B-05 and CUR-01 needs.
from book_indexer.curator import is_removed  # noqa: F401  (re-export)

# Per CONTEXT 'Specifics' Filter Set + RESEARCH §H-4 verbatim.
# NOTE: deliberately omits apostrophe (U+0027) so canonicals like
# "attorneys' fees" survive (CONTEXT D-04 explicit allow).
CRUFT_LEADING_CHARS: frozenset[str] = frozenset({
    '"', '“', '”',                      # straight + smart double quotes
    '‘', '’',                            # smart single quotes (NOT ''' apostrophe)
    '(', ')', '[', ']', '{', '}',       # brackets
    '•',                                  # bullet (U+2022)
    '*', '/', '\\', '_', '`', '~',      # decorators
    '#', '@', '$', '%', '^', '&',       # marks
})

OUTLINE_NUMBER_RE: re.Pattern[str] = re.compile(r"^[a-z]\.\s+")

MIN_LEN: int = 2
MAX_LEN: int = 100

# the reference corpus calibration anchor (RESEARCH §H-4 empirical).
# Plan 05-05 Wave 4 cold-build acceptance gate updates this if the source book IR
# changes; companion volumes will have their own numbers.
# Phase 8 / v1.2 raised 14 → 24 — Wave 3 dedup.py variant-loss patch + 4 verb-gerund
# lemma overrides surfaced 5 new bullet/punctuation-prefixed canonicals AND 5
# unbalanced-paren canonicals (cloud server data ) court, datum compilation ) record,
# podcast ), opinion(s, see federal rule of appellate procedure ( frap) caused by
# inflect interacting with pre-tokenized parenthesized lists. All 10 are pure
# surface-cruft, captured by the existing CRUFT_LEADING_CHARS rule (5) + the new
# Rule 4 unbalanced-paren guard (5).
EXPECTED_DROP_COUNT: int = 24


def _has_unbalanced_paren(canonical: str) -> bool:
    """Phase 8 / v1.2 Rule 4: detect canonicals with stray ``)`` or ``(``.

    Wave 3's inflect-driven variant generation surfaced 3 garbage canonicals
    where a closing paren leaked from a pre-tokenized parenthesized list:
        'cloud server data ) court'      (parsed from "Cloud Server Data) Courts")
        'datum compilation ) record'     (parsed from "Data Compilations) Records")
        'podcast )'                       (parsed from "Podcasts)")

    These are structurally diagnosable: a closing paren without a matching
    opening, or vice versa, is never a valid legal-term canonical. Lock #1
    preserved (verify() still owns locator emission); this is a render-time
    surface-cruft drop. v1.3 may fix the upstream inflect call site to prevent
    them from entering the IR in the first place.
    """
    return canonical.count("(") != canonical.count(")")


def is_cruft(canonical: str) -> bool:
    """Return True if this canonical should be dropped at render time.

    Rules (CONTEXT D-04 / B-05; RESEARCH §H-4 verified on 901 entries):
      1. Length < MIN_LEN (= 2) or > MAX_LEN (= 100) (or empty).
      2. First char in CRUFT_LEADING_CHARS (quotes, brackets, bullet,
         decorators, marks). NOTE: U+0027 apostrophe is allowed.
      3. Outline-numbered prefix `^[a-z]\\.\\s+` (e.g., 'a. scope').
      4. (v1.2) Unbalanced parens — guards against the Wave 3 inflect-driven
         variant-generation regression on parenthesized lists.
    """
    if not canonical or len(canonical) < MIN_LEN or len(canonical) > MAX_LEN:
        return True
    if canonical[0] in CRUFT_LEADING_CHARS:
        return True
    if OUTLINE_NUMBER_RE.match(canonical):
        return True
    if _has_unbalanced_paren(canonical):
        return True
    return False


# ---------------------------------------------------------------------------
# CUR-01 — render-time dangling-cross-reference cleanup
# ---------------------------------------------------------------------------


# Match an `*(also: a; b; c)*` parenthetical (markdown variant emit shape from
# render/markdown.py:_render_variants — italic asterisks bracket ``(also:
# <semi-separated-list>)``). The variants are ``;`` separated per
# ``_VARIANT_SEP``. Capture group 1 = inner variant list.
_ALSO_PARENTHETICAL_RE: re.Pattern[str] = re.compile(
    r" \*\(also: ([^)]+)\)\*"
)

# Match a trailing ``See <target>`` clause (target through end-of-line).
# Used for surviving entries whose cross-reference target was removed.
_SEE_CLAUSE_RE: re.Pattern[str] = re.compile(
    r"(,?\s*)See(?: also)? ([^\n,]+?)(?=[,\n]|$)"
)


def _normalize_xref_target(s: str) -> str:
    """Trim + collapse internal whitespace; case-preserved (matches removal_set)."""
    return re.sub(r"\s+", " ", s).strip()


def auto_strip_xref(
    text: str, removal_set: frozenset[str]
) -> tuple[str, list[str]]:
    """Strip ``See <removed>`` and ``(also: <list>)`` references that point
    at curator-removed canonicals from already-rendered text.

    Pure string transform; no IR mutation. Operates on the same shape that
    ``render/markdown.py`` emits:

      * ``*(also: a; b; c)*`` — italic parenthetical with semicolon-separated
        variants. Each comma-stripped variant is checked against
        ``removal_set``; matching variants are dropped. If the parenthetical
        becomes empty after dropping, the entire ``*(also: …)*`` clause is
        removed (no orphan ``*(also: )*``).
      * ``See <target>`` and ``See also <target>`` — trailing cross-reference
        clauses. If ``<target>`` (whitespace-trimmed) is in ``removal_set``
        the clause is stripped (along with its leading comma + whitespace).

    Args:
        text: rendered markdown bytes-decoded text (or DOCX run text). Both
            renderer outputs share this on-the-wire shape pre-encoding.
        removal_set: ``frozenset[str]`` of curator-confirmed removal canonicals
            (case-sensitive — the curator fixture stores canonicals as
            authored). Same set used by ``is_removed`` short-circuit.

    Returns:
        ``(cleaned_text, stripped_xrefs)`` — ``stripped_xrefs`` is a flat
        list of the targets that were removed (for coverage logging in
        ``coverage.py``'s "Curator Pass" section).
    """
    if not removal_set:
        return text, []

    stripped: list[str] = []

    # 1. *(also: …)* parenthetical surgery — drop variants whose target is
    # in removal_set; if all variants drop, remove the entire clause.
    def _scrub_also(match: re.Match[str]) -> str:
        inner = match.group(1)
        kept_variants: list[str] = []
        for v in (s.strip() for s in inner.split(";")):
            target = _normalize_xref_target(v)
            if target in removal_set:
                stripped.append(target)
                continue
            if v:
                kept_variants.append(v)
        if not kept_variants:
            # Entire clause dropped — remove leading space too (the regex
            # captured a leading " " already, so just return "").
            return ""
        return " *(also: " + "; ".join(kept_variants) + ")*"

    text = _ALSO_PARENTHETICAL_RE.sub(_scrub_also, text)

    # 2. See / See also <target> clause surgery.
    def _scrub_see(match: re.Match[str]) -> str:
        target = _normalize_xref_target(match.group(2))
        if target in removal_set:
            stripped.append(target)
            return ""
        return match.group(0)

    text = _SEE_CLAUSE_RE.sub(_scrub_see, text)

    return text, stripped
