"""v1.2.2 — Parent-aliased-standalone dedup pass for all 4 renderers.

User-reported reading experience (post-v1.2.1): the B-06 synthesized
parents emit nested children, but the SAME canonicals also appear as
standalone top-level entries directly below the parent block::

    complex
        complex case, § 1.07.13 (p. 13), …
        complex case complex, § 2.08.11 (p. 27)
        complex litigation, § 1.07.13 (p. 13), § 2.08.6 (p. 25)
        manual for complex litigation, § 2.08.6 (p. 25)
    complex case, § 1.07.13 (p. 13), …                    ← duplicate of child
    complex case complex *(also: …)*, § 2.08.11 (p. 27)   ← duplicate of child
    complex litigation, § 1.07.13 (p. 13), § 2.08.6 (p. 25)  ← duplicate of child

The reader sees the same locator-list twice for every same-first-word
child. ``manual for complex litigation`` is a child of ``complex`` whose
first word is ``manual``; its standalone IS still useful — it's the
reader's alphabetical anchor under "M" — so we KEEP it.

User-locked decision (do NOT re-discuss):

  For each B-06 synthesized parent and each of its children:

    * If the child's canonical's FIRST WORD == the parent's stem
      (case-insensitive, word-boundary respected), REMOVE the standalone
      top-level entry; the child under the parent is sufficient.
    * If the child's canonical does NOT start with the parent's stem
      (e.g. ``manual for complex litigation`` under ``complex``), KEEP
      BOTH the child and the standalone.

Variant transfer: when dropping a standalone that carries inline
``*(also: …)*`` variants, the variants are NOT lost. They are stashed in
a side-channel ``transferred_variants`` map keyed by child canonical;
the renderer's ``_render_synthetic_lines`` consults the map and emits
the variants on the surviving child line. The IR is never mutated —
``IndexEntry`` is frozen Pydantic — only the side-channel dict carries
the transfer.

Locator-mismatch safety: if the standalone's locators DIFFER from the
child's locators (set inequality), KEEP BOTH and log the decision. This
defends against IR coherence loss; in a healthy IR (the reference corpus at
v1.2.1 ship) this never fires (verified empirically: 0/93 mismatches
on the live render).

Architecture locks honored:

  Lock #1 (verify is the sole page-number emitter): this module never
    imports ``verify`` and never constructs ``Evidence``. Locators are
    pure pass-through from the upstream IR.
  Lock #2 (LLM JSON has zero page-like fields): unaffected — this is a
    render-time projection.
  Lock #3 (no anthropic / claude_agent_sdk imports): unaffected.
  Lock #5 (byte-identity replay): pure-functional + sorted iteration +
    frozen dataclass = byte-identical output across runs given the same
    input stream.

requirements_addressed: v1.2.2 parent-aliased-standalone dedup
    (post-v1.2.1 patch).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .plural_consolidation import ConsolidatedEntry

__all__ = [
    "ParentDedupResult",
    "dedupe_parent_aliased_standalones",
]


# Stream-payload kind tag — same shape as plural_consolidation.StreamItem.
# Re-declared locally so the parent-dedup module is self-contained
# (matches the cross_refs.py / plural_consolidation.py style).
StreamItem = tuple[str, str, object]


# Reason tags returned in ``ParentDedupResult.reason`` for traceability.
_REASON_PREFIX_MATCH: Literal["prefix_match"] = "prefix_match"
_REASON_DIFFERENT_FIRST_WORD: Literal["different_first_word"] = "different_first_word"
_REASON_LOCATOR_MISMATCH: Literal["different_locators_kept_both"] = (
    "different_locators_kept_both"
)


@dataclass(frozen=True)
class ParentDedupResult:
    """Records each dedup decision for traceability + tests.

    Fields:
        parent_stem: the B-06 synthesized parent's stem (e.g. ``"complex"``).
        child_canonical: the sibling canonical under that parent (e.g.
            ``"complex case"``).
        standalone_dropped: True iff the standalone top-level entry was
            removed; False iff the standalone was kept (different first
            word, locator mismatch, or no standalone existed).
        reason: ``"prefix_match"`` (drop), ``"different_first_word"``
            (kept — alphabetical anchor), or
            ``"different_locators_kept_both"`` (kept — IR coherence guard).
    """

    parent_stem: str
    child_canonical: str
    standalone_dropped: bool
    reason: str


# ---------------------------------------------------------------------------
# Word-boundary first-word match
# ---------------------------------------------------------------------------


def _first_word(canonical: str) -> str:
    """Return the lowercased first whitespace-separated token of ``canonical``.

    Empty string for the empty input. Used as the word-boundary check
    against a parent stem so that ``"complex case".startswith("complex ")``
    is True but ``"compliance case"`` is False — substring matching would
    falsely fire for the latter.
    """
    if not canonical:
        return ""
    parts = canonical.split()
    if not parts:
        return ""
    return parts[0].lower()


def _starts_with_parent_stem(child_canonical: str, parent_stem: str) -> bool:
    """Return True iff the child's first word equals the parent stem
    (case-insensitive). Both arguments are normalized to lowercase before
    the equality check.

    Edge case: a child whose canonical is EXACTLY the parent stem (e.g.
    a child ``"complex"`` under parent ``"complex"``) returns True — but
    the synthesizer's rule (b) (``stem not in canon_set``) guarantees no
    such pairing exists in practice. The check returns True for safety;
    it would result in dropping the redundant standalone (correct).
    """
    if not child_canonical or not parent_stem:
        return False
    return _first_word(child_canonical) == parent_stem.lower()


# ---------------------------------------------------------------------------
# Standalone-entry index (canonical → stream slot)
# ---------------------------------------------------------------------------


def _build_standalone_index(stream: list[StreamItem]) -> dict[str, int]:
    """Walk the stream and return a map of standalone canonical → index.

    A "standalone" is any item with ``kind == "entry"`` (an IndexEntry
    in the merged stream). Synthetic parents (``kind == "synth"``),
    cross-refs (``kind == "xref"``), and consolidated plural pairs
    (``kind == "consolidated"``) are NOT standalones for the purpose of
    this dedup.

    A ``ConsolidatedEntry`` whose ``primary_canonical`` matches a child
    canonical IS treated as a standalone (the consolidated form is the
    user-visible top-level line). Both kinds are indexed.

    Map values are the index in the stream; the caller marks slots as
    consumed by index.
    """
    index: dict[str, int] = {}
    for i, (_sort, kind, payload) in enumerate(stream):
        if kind == "entry":
            canon = getattr(payload, "canonical", None)
            if isinstance(canon, str):
                # First occurrence wins on duplicate canonicals (defensive;
                # IR invariants guarantee uniqueness).
                index.setdefault(canon, i)
        elif kind == "consolidated":
            assert isinstance(payload, ConsolidatedEntry)
            # The consolidated line's user-visible canonical is the
            # singular, but the display string carries the (s)/(es)/(ies)
            # ending. We index by primary_canonical (the bare singular)
            # because that's what would match the child canonical.
            index.setdefault(payload.primary_canonical, i)
    return index


# ---------------------------------------------------------------------------
# Locator-equality (set semantics — order-independent)
# ---------------------------------------------------------------------------


def _locator_set(locators: Iterable[object]) -> frozenset[tuple[str, str]]:
    """Return a frozenset of ``(section_ref, folio)`` pairs from an
    iterable of ``Locator`` instances. Set semantics deliberately
    discard ordering and any incidental ``evidence_id`` differences —
    we want to know whether the SAME page-citations appear on both
    sides, not whether the IR happens to have them in the same order.

    A standalone IndexEntry's locators are the result of Phase 4
    canonicalization; the synth child's locators are pulled from the
    SAME IndexEntry by ``_render_synthetic_lines`` via
    ``entries_by_canonical[sib_canonical].locators``. By construction,
    therefore, the locator sets MUST be identical when the child is
    the standalone. The set check is the safety net — if the IR ever
    becomes incoherent (different IndexEntry instances with the same
    canonical, or a future stream-mutation pass that diverges them),
    the keep-both branch fires.
    """
    out: set[tuple[str, str]] = set()
    for loc in locators:
        section_ref = getattr(loc, "section_ref", None)
        folio = getattr(loc, "folio", None)
        if isinstance(section_ref, str) and isinstance(folio, str):
            out.add((section_ref, folio))
    return frozenset(out)


def _standalone_locator_set(payload: object) -> frozenset[tuple[str, str]]:
    """Pull the locator-set off either an ``IndexEntry`` or a
    ``ConsolidatedEntry`` payload (both are valid standalone shapes
    in the merged stream).
    """
    if isinstance(payload, ConsolidatedEntry):
        return _locator_set(payload.locators)
    return _locator_set(getattr(payload, "locators", []) or [])


def _standalone_variants(payload: object) -> tuple[str, ...]:
    """Pull pre-existing variants off a standalone payload.

    ``ConsolidatedEntry`` does NOT carry variants by design (the
    consolidation pass refuses to merge variant-bearing entries — see
    ``plural_consolidation._try_consolidate_pair`` rule 3), so the
    consolidated branch always returns ``()``. ``IndexEntry`` carries
    a ``variants: list[str]`` field; convert to tuple for immutability
    in the side-channel transfer map.
    """
    if isinstance(payload, ConsolidatedEntry):
        return tuple(payload.variants)
    raw = getattr(payload, "variants", None)
    if not raw:
        return ()
    return tuple(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dedupe_parent_aliased_standalones(
    stream: Iterable[StreamItem],
    entries_by_canonical: Mapping[str, object] | None = None,
) -> tuple[list[StreamItem], dict[str, tuple[str, ...]], list[ParentDedupResult]]:
    """Drop standalone entries that duplicate B-06 synthesized children.

    Args:
        stream: the merged ``(sort_key, kind, payload)`` stream as
            returned by ``consolidate_plural_pairs``. Pass the entire
            stream — this function is a single-pass post-pass with O(n)
            standalone-index lookup.
        entries_by_canonical: optional dict mapping ``canonical`` →
            ``IndexEntry``. Used to compute the CHILD's locators
            independently from the standalone's locators — the locator
            comparison is otherwise tautological (the renderer's
            ``_render_synthetic_lines`` pulls child locators from this
            very dict, so passing it here lets us audit the actual
            source of truth for the child's page-citations). When
            ``None``, the locator-mismatch guard degrades to a
            tautological self-check (always-equal); the dedup still
            fires correctly but the safety net is dormant.

    Returns:
        A 3-tuple ``(new_stream, transferred_variants, decisions)``:

          * ``new_stream`` — the stream with prefix-matching same-locator
            standalones removed. All other items pass through verbatim
            in their original relative order.
          * ``transferred_variants`` — dict ``{child_canonical: variants}``
            for every standalone whose ``*(also: …)*`` variants need to
            be re-emitted on the surviving child line under the
            synthetic parent. The renderer's ``_render_synthetic_lines``
            consults this map; entries without an override key emit no
            variants (current behavior).
          * ``decisions`` — list of ``ParentDedupResult`` records, one
            per (parent, child) pair examined. Used by the SUMMARY
            telemetry; render output is otherwise unaffected.

    Algorithm:

      1. Materialize the stream as a list and build the standalone-by-
         canonical index (entry + consolidated payloads only).
      2. For each ``"synth"`` item, walk its ``sibling_canonicals``:
         a. ``_first_word(child) == synth.stem.lower()`` decides PREFIX vs
            DIFFERENT-FIRST-WORD. Different first word → keep both.
         b. PREFIX path: look up the standalone slot. If absent (child
            already unique), record ``prefix_match`` decision but no
            removal.
         c. PREFIX path with standalone present: compare locator sets.
            Mismatch → keep both, record
            ``different_locators_kept_both`` decision and log to stderr.
            Match → mark the standalone slot for removal, transfer
            variants if any, record ``prefix_match`` decision with
            ``standalone_dropped=True``.
      3. Emit the new stream by skipping every consumed slot in original
         order.

    Determinism: pure-functional, no global mutation, sorted iteration
    over ``sibling_canonicals`` (already sorted by the synthesizer; we
    re-sort defensively for byte-identity in the decisions list). Lock
    #5 byte-identity by-construction.

    Idempotence: running the pass twice on the same stream yields the
    same output. The second pass finds no remaining same-first-word
    standalones to drop (they've been removed) and emits an empty
    ``transferred_variants`` map plus a decisions list containing only
    ``different_first_word`` and ``prefix_match`` (with
    ``standalone_dropped=False``) entries.
    """
    items: list[StreamItem] = list(stream)
    standalone_index = _build_standalone_index(items)
    consumed: set[int] = set()
    transferred_variants: dict[str, tuple[str, ...]] = {}
    decisions: list[ParentDedupResult] = []

    # Sort synth-iteration order by stem for byte-determinism in the
    # decisions list. The output stream order is unaffected — items are
    # emitted in their original positions; only the decisions list
    # ordering depends on this sort.
    synth_items: list[tuple[int, object]] = []
    for i, (_sort, kind, payload) in enumerate(items):
        if kind == "synth":
            synth_items.append((i, payload))
    synth_items.sort(key=lambda pair: getattr(pair[1], "stem", ""))

    for _synth_idx, synth in synth_items:
        stem = getattr(synth, "stem", "")
        siblings = getattr(synth, "sibling_canonicals", ()) or ()
        for child_canonical in sorted(siblings):
            if not _starts_with_parent_stem(child_canonical, stem):
                decisions.append(
                    ParentDedupResult(
                        parent_stem=stem,
                        child_canonical=child_canonical,
                        standalone_dropped=False,
                        reason=_REASON_DIFFERENT_FIRST_WORD,
                    )
                )
                continue

            # Same-first-word — candidate for dedup.
            standalone_slot = standalone_index.get(child_canonical)
            if standalone_slot is None or standalone_slot in consumed:
                # No standalone exists (or already consumed by a prior
                # synth iteration — possible if two synth parents share
                # a sibling, e.g. ``complex case`` could be a child of
                # both ``complex`` AND ``case``). Record the decision
                # without dropping anything; child remains under parent.
                decisions.append(
                    ParentDedupResult(
                        parent_stem=stem,
                        child_canonical=child_canonical,
                        standalone_dropped=False,
                        reason=_REASON_PREFIX_MATCH,
                    )
                )
                continue

            standalone_payload = items[standalone_slot][2]
            standalone_locs = _standalone_locator_set(standalone_payload)

            # Pull the CHILD's locators from entries_by_canonical (the
            # SAME source ``_render_synthetic_lines`` uses to format
            # the child line under the synth parent). This makes the
            # mismatch check meaningful — without it, both sides come
            # from ``items[standalone_slot]`` and the comparison is
            # tautological. With it, we'd catch a future divergence
            # where the same canonical resolves to different IndexEntry
            # instances in the dict vs. the stream (a defense against
            # IR-coherence loss).
            if entries_by_canonical is not None:
                child_entry = entries_by_canonical.get(child_canonical)
                if child_entry is not None:
                    child_locs = _locator_set(
                        getattr(child_entry, "locators", []) or []
                    )
                else:
                    # Child not in the rendering dict — the synth's
                    # union locators are the renderer's fallback (see
                    # ``_render_synthetic_lines`` else-branch). Use the
                    # synth-union as the child's locator source.
                    child_locs = _locator_set(
                        getattr(synth, "locators", []) or []
                    )
            else:
                # Degraded mode: tautological self-check (always equal).
                child_locs = standalone_locs

            if standalone_locs != child_locs:  # pragma: no cover — IR-coherent
                import sys

                print(
                    f"[parent_dedup] LOCATOR MISMATCH — keeping both: "
                    f"parent={stem!r} child={child_canonical!r}",
                    file=sys.stderr,
                )
                decisions.append(
                    ParentDedupResult(
                        parent_stem=stem,
                        child_canonical=child_canonical,
                        standalone_dropped=False,
                        reason=_REASON_LOCATOR_MISMATCH,
                    )
                )
                continue

            # All gates passed — drop the standalone, transfer variants.
            consumed.add(standalone_slot)
            variants = _standalone_variants(standalone_payload)
            if variants:
                # First write wins on duplicate child canonicals — same
                # rationale as ``standalone_index.setdefault`` above.
                transferred_variants.setdefault(child_canonical, variants)
            decisions.append(
                ParentDedupResult(
                    parent_stem=stem,
                    child_canonical=child_canonical,
                    standalone_dropped=True,
                    reason=_REASON_PREFIX_MATCH,
                )
            )

    # Emit the stream with consumed slots skipped.
    new_stream: list[StreamItem] = [
        item for i, item in enumerate(items) if i not in consumed
    ]
    return new_stream, transferred_variants, decisions
