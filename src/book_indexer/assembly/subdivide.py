"""ASM-04 sub-divide rule per D-05.

When a main IndexEntry has >7 undifferentiated section locators, subdivide:

  1. Prefer Phase 3a's ``suggested_subentries`` (LLM-guided / pattern-
     guided when present).
  2. Pad with top-N=5 co-occurring noun-phrase candidates that share
     ≥2 distinct sub-sections with the parent (D-05 — stricter than
     See-also's ≥3-sub-section threshold).
  3. Build sub-entries: ``locators = parent_secs ∩ candidate_secs``.
  4. ``residual`` = parent locators NOT covered by any sub-entry.
  5. If residual still >7, iterate once more with N=3 and a stricter
     candidate pool (the residual sub-sections only).
  6. If still >7 after iteration depth 2, raise
     :class:`OversizeAfterIterationError` — bounded recursion ceiling
     per D-05 + RESEARCH §H-13.

AUTHORITATIVE source for ``noun_phrase_pool``: Phase 3a's
``artifacts/concepts/noun_phrase_ch{1..5}.json`` candidates ONLY. NER and
doctrinal candidates are EXPLICITLY excluded (D-05 final note): they are
typically already canonical entries themselves and would create
self-referential sub-entries.

This module is a pure function — no I/O, no ``verify()`` calls, no
mutation of inputs. The caller is responsible for filtering the pool
to noun_phrase candidates only.

Architecture Lock #1: this module never constructs ``Evidence``. It
constructs ``SubEntry`` objects whose ``locators`` are PROJECTIONS of
existing ``Locator`` rows passed in via ``noun_phrase_pool``.

requirements_addressed: ASM-04 (>7-locator subdivide rule).
"""
from __future__ import annotations

from collections.abc import Iterable

from book_indexer.tables.ir import Locator

from .errors import OversizeAfterIterationError
from .ir import SubEntry

# D-05 constants. Hard-coded; these are NOT runtime parameters.
_OVERSIZE_THRESHOLD = 7
_CO_OCCURRENCE_MIN = 2  # ≥2 distinct sub-sections (stricter than See-also)
_MAX_ITER_DEPTH = 2
_ITER_PASS_2_N = 3  # Pass 2 picks 3 secondary sub-entries


def compute_co_occurrence(
    parent_secs: set[str],
    candidate_locators: Iterable[Locator],
) -> int:
    """Count distinct ``section_ref`` values shared between parent and candidate.

    Multiple locators on the same ``section_ref`` count as ONE shared
    sub-section (set semantics). This matches the D-05 "≥2 distinct
    sub-section" threshold definition.
    """
    cand_secs = {loc.section_ref for loc in candidate_locators}
    return len(parent_secs & cand_secs)


def _select_top_n(
    canonical_id: str,
    already_chosen: set[str],
    parent_secs: set[str],
    noun_phrase_pool: dict[str, list[Locator]],
    n: int,
) -> list[str]:
    """Pick top-N candidate ids by co-occurrence count.

    Filters: skip ``canonical_id`` (a canonical never sub-divides itself);
    skip ids already chosen; require ``co >= _CO_OCCURRENCE_MIN``.

    Sort key: ``(-co, id)`` — higher co-count first; alphabetical
    tiebreaker (deterministic).
    """
    scored: list[tuple[int, str]] = []
    for np_id, np_locators in noun_phrase_pool.items():
        if np_id == canonical_id or np_id in already_chosen:
            continue
        co = compute_co_occurrence(parent_secs, np_locators)
        if co >= _CO_OCCURRENCE_MIN:
            scored.append((co, np_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [np_id for _, np_id in scored[:n]]


def _build_sub_entries(
    chosen: list[str],
    parent_secs: set[str],
    noun_phrase_pool: dict[str, list[Locator]],
) -> tuple[list[SubEntry], set[str]]:
    """Materialize chosen ids into ``SubEntry`` objects.

    Each SubEntry's ``locators`` are the candidate's locators filtered
    to the parent's section_refs (intersection). If the intersection is
    empty (e.g., a chosen suggested_subentry has no overlap with parent),
    the SubEntry is dropped silently.

    Returns:
        ``(sub_entries, covered_secs)`` — sub_entries sorted alphabetically
        by sort_key; covered_secs is the union of section_refs touched
        by the emitted sub-entries.
    """
    sub_entries: list[SubEntry] = []
    covered: set[str] = set()
    for sub_id in chosen:
        sub_locators = [
            loc
            for loc in noun_phrase_pool.get(sub_id, [])
            if loc.section_ref in parent_secs
        ]
        if not sub_locators:
            continue
        # Sort locators by (section_ref, folio) for determinism
        sub_locators_sorted = sorted(
            sub_locators, key=lambda loc: (loc.section_ref, loc.folio)
        )
        sub_entries.append(
            SubEntry(
                text=sub_id,
                sort_key=sub_id.lower(),
                locators=sub_locators_sorted,
            )
        )
        covered.update(loc.section_ref for loc in sub_locators)
    sub_entries.sort(key=lambda e: e.sort_key)
    return sub_entries, covered


def subdivide_oversize(
    canonical_id: str,
    parent_locators: list[Locator],
    suggested_subentries: list[str],
    noun_phrase_pool: dict[str, list[Locator]],
    n: int = 5,
) -> tuple[list[SubEntry], list[Locator]]:
    """Subdivide an oversize parent (>7 locators) per D-05.

    Args:
        canonical_id: The parent IndexEntry.id; excluded from its own
            sub-entry pool.
        parent_locators: All locators attached to the parent (the
            "undifferentiated" set we want to subdivide).
        suggested_subentries: Phase 3a-supplied sub-entry hints. Used
            verbatim, truncated to ``n``. May be empty.
        noun_phrase_pool: ``{np_id: [Locator, ...]}`` of noun-phrase
            candidates discovered by Phase 3a. Caller filters: NER and
            doctrinal candidates MUST NOT be in this pool (D-05 final
            note).
        n: Top-N for the primary pass. Default 5 (D-05). Pass 2 always
            uses ``_ITER_PASS_2_N = 3``.

    Returns:
        ``(sub_entries, residual_parent_locators)``. When parent is NOT
        oversize, returns ``([], parent_locators)`` unchanged.

    Raises:
        OversizeAfterIterationError: residual still >7 after iteration
        depth 2 — bounded recursion ceiling per D-05 + RESEARCH §H-13.
    """
    if len(parent_locators) <= _OVERSIZE_THRESHOLD:
        return [], list(parent_locators)

    parent_secs = {loc.section_ref for loc in parent_locators}

    # ---- Pass 1 ------------------------------------------------------------
    chosen: list[str] = list(suggested_subentries[:n])
    already: set[str] = set(chosen)

    if len(chosen) < n:
        padding = _select_top_n(
            canonical_id=canonical_id,
            already_chosen=already,
            parent_secs=parent_secs,
            noun_phrase_pool=noun_phrase_pool,
            n=n - len(chosen),
        )
        chosen.extend(padding)
        already.update(padding)

    sub_entries, covered = _build_sub_entries(chosen, parent_secs, noun_phrase_pool)
    residual = [
        loc for loc in parent_locators if loc.section_ref not in covered
    ]
    iter_depth = 1

    # ---- Pass 2 (iter depth 2) — only if still oversize --------------------
    while (
        len(residual) > _OVERSIZE_THRESHOLD and iter_depth < _MAX_ITER_DEPTH
    ):
        already.update(e.text for e in sub_entries)
        residual_secs = {loc.section_ref for loc in residual}
        extra = _select_top_n(
            canonical_id=canonical_id,
            already_chosen=already,
            parent_secs=residual_secs,
            noun_phrase_pool=noun_phrase_pool,
            n=_ITER_PASS_2_N,
        )
        extra_entries, extra_covered = _build_sub_entries(
            extra, residual_secs, noun_phrase_pool
        )
        # Merge + re-sort alphabetically
        sub_entries = sorted(
            sub_entries + extra_entries, key=lambda e: e.sort_key
        )
        residual = [
            loc for loc in residual if loc.section_ref not in extra_covered
        ]
        iter_depth += 1

    # ---- Bounded-recursion ceiling -----------------------------------------
    if len(residual) > _OVERSIZE_THRESHOLD:
        raise OversizeAfterIterationError(
            f"canonical {canonical_id!r} still has {len(residual)} residual "
            f"locators after iteration depth {iter_depth}; bounded recursion "
            f"exhausted (RESEARCH §H-13)."
        )

    # Sort residual deterministically by (section_ref, folio).
    residual_sorted = sorted(residual, key=lambda loc: (loc.section_ref, loc.folio))
    return sub_entries, residual_sorted
