"""UAT 08-1 — Renderer-side alphabetical head-noun cross-references.

User-reported gap (UAT 08 Test 1, 2026-05-01): a reader looking up
``interrogatories`` alphabetically in ``artifacts/render/index.md``
finds nothing — the term was canonicalized under ``special interrogatory``
during Phase 4, and no alphabetical anchor was emitted for the head noun.
The same shape affects ~30+ multi-word canonicals (``social network
picture`` → no ``picture`` anchor; ``hearsay exception`` → no ``hearsay``
anchor — though ``hearsay`` is already a synthesized stem in v1.0 via
B-06; this module does NOT re-emit synthesized stems).

Design (auto-detect at render time, no curator-gate):

For each rendered ``IndexEntry`` whose canonical is multi-word (≥ 2
tokens), extract the rightmost token as the head noun. If the head noun
(and/or its plural form) is NOT already represented as a top-level
canonical / synthesized stem AND passes the substantive-noun filter,
emit a synthetic cross-ref entry of the shape::

    interrogatories. See special interrogatory.

placed at its natural alphabetical position alongside primary entries.

The cross-ref is a render-time projection — no IR row is created. The
verifier sees nothing new (Lock #1 untouched). The Phase 4
``IndexTree`` is read-only consumer territory. Re-running the pipeline
twice produces byte-identical cross-refs because the input IR is byte-
identical and the derivation is pure-functional + sorted.

Substantive-noun filter (heuristic, biased toward FEWER cross-refs):

  - Head noun length must be ≥ 4 chars (skip ``fee``, ``law``, ``tax``).
  - Head noun must NOT be in the stop-noun set (``thing``, ``way``,
    ``part``, ``type``, ``kind``, ``case``, ``fact``, ``form``,
    ``issue``, ``matter``, ``point``, ``right``, ``rule``, ``side``,
    ``step``, ``term``, ``use``, ``view``).
  - Head noun must NOT already exist as a top-level canonical.
  - Head noun must NOT already exist as a synthesized stem (B-06).
  - Head noun must NOT itself be the canonical it would point at
    (defensive — single-word canonicals never reach this path because of
    the multi-word gate, but keep the assertion shape simple).

For each surviving head, the cross-ref points at the FIRST primary
canonical (alphabetically) that ends in the head — this is the
deterministic "primary canonical" choice. When a head and its plural
inflection are both eligible, both cross-refs are emitted (e.g.,
``interrogatory`` AND ``interrogatories`` both point at
``special interrogatory``) so an alphabetical reader hits the cross-ref
regardless of singular/plural lookup form.

Lock #1: this module never imports ``verify`` or constructs ``Evidence``.
Lock #5: deterministic by construction — sorted iterations everywhere.

requirements_addressed: UAT 08-1 (head-noun alphabetical cross-refs).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import inflect

from .ir import IndexEntry, SyntheticEntry

__all__ = [
    "CrossRefEntry",
    "STOP_HEADS",
    "MIN_HEAD_LENGTH",
    "derive_cross_refs",
]


# Module-level inflect engine — mirrors assembly.dedup._INFLECT, sized to
# the same hot-loop constraint (1k+ entries). Re-instantiating per call
# would dominate the render hot-path on companion volumes.
_INFLECT = inflect.engine()


# Substantive-noun gate: head nouns shorter than this are too generic.
MIN_HEAD_LENGTH: int = 4


# Stop-noun set — heads we never emit cross-refs for, even when ≥ 4 chars.
# Bias is toward fewer cross-refs over noise per UAT 08-1 design.
STOP_HEADS: frozenset[str] = frozenset({
    "thing",
    "way",
    "part",
    "type",
    "kind",
    "case",
    "fact",
    "form",
    "issue",
    "matter",
    "point",
    "right",
    "rule",
    "side",
    "step",
    "term",
    "use",
    "view",
})


@dataclass(frozen=True)
class CrossRefEntry:
    """Render-time projection: alphabetical head-noun → primary canonical.

    ``head`` is the alphabetical anchor (e.g. ``"interrogatories"``).
    ``primary_canonical`` is the IndexEntry.canonical the cross-ref
    points to (e.g. ``"special interrogatory"``).
    ``sort_key`` is ``head.lower()`` — the merge-loop in the markdown
    renderer sorts on this key so the cross-ref lands at the natural
    alphabetical position. Frozen so the merge loop's tuple-sort is
    deterministic (Lock #5).
    """

    head: str
    primary_canonical: str
    sort_key: str


def _is_substantive_head(head: str) -> bool:
    """Substantive-noun filter — see module docstring."""
    if len(head) < MIN_HEAD_LENGTH:
        return False
    if head.lower() in STOP_HEADS:
        return False
    return True


def _head_noun(canonical: str) -> str | None:
    """Return the rightmost token of a multi-word canonical, lowercased.

    Returns ``None`` if the canonical is single-word (no cross-ref needed —
    a single-word canonical IS its own alphabetical anchor) or empty.
    """
    tokens = canonical.split()
    if len(tokens) < 2:
        return None
    return tokens[-1].lower()


def _plural_of(head: str) -> str | None:
    """Return the plural form of ``head`` if distinct, else None.

    Uses the same case-tolerant guard introduced by the UAT 08-1b dedup
    fix: ``singular_noun(head)`` returns False iff ``head`` is singular,
    in which case ``_INFLECT.plural(head)`` is the correct plural form.
    If ``head`` is already plural (singular_noun returns a string), no
    plural form is emitted (``head`` itself IS the plural, which the
    caller will already have picked up as the anchor).
    """
    if _INFLECT.singular_noun(head) is False:  # pyright: ignore[reportArgumentType]  # inflect stub
        plural = _INFLECT.plural(head)  # pyright: ignore[reportArgumentType]  # inflect stub
        if plural and plural.lower() != head.lower():
            return plural
    return None


def derive_cross_refs(
    entries: Iterable[IndexEntry],
    synthetics: Iterable[SyntheticEntry],
) -> list[CrossRefEntry]:
    """Derive head-noun cross-refs for every multi-word canonical.

    Args:
        entries: the IR's ``tree.entries`` (read-only).
        synthetics: B-06 synthesized stems (read-only).

    Returns:
        A sorted, deduplicated list of ``CrossRefEntry`` instances. The
        sort is by ``(sort_key, head, primary_canonical)`` so the output
        is byte-deterministic (Lock #5).

    Algorithm:

    1. Build a set of all top-level canonicals (lowercased) and a set of
       all synthesized stems (lowercased) — these are the "already
       represented" alphabetical anchors. Heads matching either are
       skipped.
    2. For each multi-word canonical, derive the head noun. If the head
       fails the substantive filter or is already represented, skip.
    3. Pick the FIRST canonical (alphabetically) that ends in the head as
       the primary cross-ref target. Multiple multi-word canonicals can
       share a head (``social network picture``, ``digital image
       picture`` both → ``picture``); the alphabetically first one wins
       for determinism.
    4. Emit a CrossRefEntry for the head AND its plural form (if the
       plural is distinct AND also passes the same already-represented
       gate). This catches the smoking-gun:
       ``interrogatory`` (singular) AND ``interrogatories`` (plural)
       both point at ``special interrogatory``.
    5. Final list is sorted by sort_key (lowercase) for the merge loop.

    Determinism: sorted iterations + frozen dataclass + module-level
    inflect engine = byte-identical output across runs (Lock #5).
    """
    entries_list = list(entries)
    synth_list = list(synthetics)

    # All "already represented" alphabetical anchors (case-folded).
    canonical_set: set[str] = {e.canonical.lower() for e in entries_list}
    stem_set: set[str] = {s.stem.lower() for s in synth_list}
    already_represented: set[str] = canonical_set | stem_set

    # For each candidate head, find the first (alphabetical) primary
    # canonical that ends in the head. Sort entries by canonical first
    # so "first alphabetical" is deterministic.
    sorted_entries = sorted(entries_list, key=lambda e: e.canonical.lower())

    # head_lower → primary_canonical (string). First write wins (the
    # alphabetically-first multi-word canonical ending in this head).
    head_to_primary: dict[str, str] = {}
    for entry in sorted_entries:
        head = _head_noun(entry.canonical)
        if head is None:
            continue
        if not _is_substantive_head(head):
            continue
        if head in already_represented:
            continue
        # First-write-wins: the alphabetical iteration order ensures
        # determinism. Don't overwrite if a prior entry claimed this head.
        if head not in head_to_primary:
            head_to_primary[head] = entry.canonical

    # Emit one CrossRefEntry per (head_form, primary). For each base head,
    # also emit the plural form if it's distinct and not already
    # represented. Use the head's display form (lowercased; matches the
    # alphabetical sort convention used by markdown.py's _entry_letter).
    cross_refs: list[CrossRefEntry] = []
    for head, primary in head_to_primary.items():
        cross_refs.append(
            CrossRefEntry(head=head, primary_canonical=primary, sort_key=head)
        )
        plural = _plural_of(head)
        if plural is None:
            continue
        plural_lower = plural.lower()
        if plural_lower in already_represented:
            continue
        if plural_lower in head_to_primary:
            # Another multi-word canonical already directly anchors the
            # plural form — don't double-emit.
            continue
        if not _is_substantive_head(plural_lower):
            continue
        cross_refs.append(
            CrossRefEntry(
                head=plural_lower,
                primary_canonical=primary,
                sort_key=plural_lower,
            )
        )

    # Final sort for determinism (Lock #5). Tie-break by primary_canonical
    # so the output is fully ordered.
    cross_refs.sort(key=lambda c: (c.sort_key, c.head, c.primary_canonical))
    return cross_refs
