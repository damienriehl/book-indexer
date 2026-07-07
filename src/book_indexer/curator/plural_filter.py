"""CUR-03 plural-variant filter — drop pure-plural inflections from `(also: ...)` lists.

Render-time projection only. The IR (``IndexEntry.variants``) is unchanged;
callers (``render/markdown.py:_filter_variants`` and the parallel function
in ``render/docx.py``) consult this predicate before emitting the variant.
Pattern mirrors D-04 (remove) and D-05 (recapitalize): symbolic,
deterministic, IR-untouched.

See Phase 7 CONTEXT D-13 for design rationale. The BUILDING phases (1-4)
stay untouched — this is the curation pass.

CRITICAL: this filters DISPLAY TEXT only. ``IndexEntry.locators`` is never
touched; every section + page that contained the plural form is still
cited under the canonical (Phase 4's lemma bucketing already merged them).

requirements_addressed: CUR-03.
"""
from __future__ import annotations

import inflect

# Module-level engine — inflect.engine() is stateful but cheap; reuse a
# singleton to avoid re-allocating on every variant check (called O(variants)
# times per render, ~3000 times for the reference corpus).
#
# `classical(all=True)` activates Latin/Greek-derived classical plurals
# (memoranda, appendices, dicta, media, …) which are common in legal writing
# and were missed by inflect's default anglicized output (memorandums,
# appendixes, dictums, mediums). Verified on inflect 7.x — does NOT break
# regular English plurals (objections, cases) or English irregulars (mice,
# children, feet).
_ENGINE = inflect.engine()
_ENGINE.classical(all=True)

# Latin/Greek plurals that classical-mode `inflect` STILL doesn't recognize
# (gaps as of inflect 7.x). Each entry maps a singular form to its
# legitimate Latin plural; the predicate below consults this dict before
# falling back to inflect. Add new entries when CUR-03 spot-checks surface
# leakage in cold-build review.
_LATIN_PLURAL_EXTRAS: dict[str, str] = {
    "addendum": "addenda",
    "corrigendum": "corrigenda",
    "erratum": "errata",
    "forum": "fora",
    "stratum": "strata",
    "datum": "data",  # though "data" is often treated as singular in modern usage
    "curriculum": "curricula",
    "compendium": "compendia",
    "symposium": "symposia",
    "millennium": "millennia",
    "ovum": "ova",
}
# Reverse map for quick singular lookup: variant_lower -> canonical_lower.
_LATIN_SINGULAR_LOOKUP: dict[str, str] = {
    plural: singular for singular, plural in _LATIN_PLURAL_EXTRAS.items()
}


def is_droppable_plural_variant(
    canonical: str,
    variant: str,
    keep_set: frozenset[str],
) -> bool:
    """True iff the variant is a pure English plural inflection of the canonical
    AND the canonical is NOT in the curator-confirmed exclusion list.

    Detection rule:
      - Drop iff ``canonical.lower()`` is NOT in ``keep_set`` AND
        (``inflect.singular_noun(variant) == canonical``
         OR ``inflect.plural(canonical) == variant``)
      - Comparison is case-insensitive (both sides ``.lower()``-ed before
        equality).

    Returns False on:
      - empty canonical or variant
      - canonical and variant differing only by case (caller's existing rule)
      - canonical or its plural-form being in keep_set
      - any pair that's not an English plural inflection (lemma differs)

    Args:
        canonical: the canonical entry name (Phase 4's chosen surface form).
        variant: a candidate variant string from ``IndexEntry.variants``.
        keep_set: ``frozenset`` of canonicals (case-folded by caller) whose
            plural variants MUST be preserved (legal-domain-distinct plurals
            like ``damages``, ``findings``, ``costs``).

    Returns:
        ``True`` if the variant should be DROPPED from the rendered
        ``(also: …)`` parenthetical, else ``False`` (keep variant).
    """
    if not canonical or not variant:
        return False
    c_lower = canonical.lower()
    v_lower = variant.lower()
    if c_lower == v_lower:
        # Same surface (case-only delta) — handled by existing _filter_variants
        # case-only check; not our concern. Return False rather than crash.
        return False
    if c_lower in keep_set:
        return False
    # Direction Z: hardcoded Latin-plural extras (inflect.classical doesn't
    # cover all legal-Latin plurals; the residual gaps live in _LATIN_*).
    # Forward (variant is plural form per the extras map):
    if _LATIN_SINGULAR_LOOKUP.get(v_lower) == c_lower:
        return v_lower not in keep_set
    # Reverse (canonical happens to be the plural per the extras map):
    if _LATIN_SINGULAR_LOOKUP.get(c_lower) == v_lower:
        return c_lower not in keep_set
    # Direction A: variant is plural, canonical is singular form
    #   (inflect.singular_noun("hearts") == "heart")
    singular_of_v = _ENGINE.singular_noun(v_lower)
    if singular_of_v and singular_of_v == c_lower:
        # If the plural form (i.e., the variant) is curator-protected, keep it.
        return v_lower not in keep_set
    # Direction B: variant is singular, canonical is plural form
    #   (inflect.singular_noun("hearings") == "hearing")
    # Covers irregular reverse cases where inflect.plural() of an
    # already-plural word does NOT round-trip (e.g., plural("criteria")
    # == "criterias", which fails Direction C below).
    singular_of_c = _ENGINE.singular_noun(c_lower)
    if singular_of_c and singular_of_c == v_lower:
        # Also exclude if canonical (the plural) is in keep_set.
        return c_lower not in keep_set
    # Direction C: canonical is singular, variant is plural form
    #   (inflect.plural("heart") == "hearts")
    plural_of_c = _ENGINE.plural(c_lower)
    if plural_of_c and plural_of_c == v_lower:
        # Also exclude if the plural-form (i.e., the variant) is in keep_set
        # — covers the case where keep_set lists the plural form
        # (e.g., "damages" with canonical="damage").
        return v_lower not in keep_set
    return False
