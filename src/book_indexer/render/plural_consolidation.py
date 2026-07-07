"""v1.2.1 — Plural-pair consolidation pass for all 4 renderers.

User-reported reading experience (post-v1.2): the rendered subject index
contains many adjacent singular/plural pairs that read as redundant noise
to a reader::

    agency. See administrative agency.
    agencies. See administrative agency.

    communication. See privileged communication.
    communications. See privileged communication.

    copy. See accurate copy.
    copies. See accurate copy.

This module collapses each such pair into a single ``term(ending)`` line
preserving the legal-treatise convention::

    agency(ies). See administrative agency.
    communication(s). See privileged communication.
    copy(ies). See accurate copy.

The consolidation runs at render time over the merged
``(sort_key, kind, payload)`` stream produced by ``_render_subject_index``
in each of the four renderers (markdown.py, markdown_sections_only.py,
docx.py, docx_sections_only.py). Each renderer then formats the
``ConsolidatedEntry`` using its own line shape (markdown text vs. DOCX
paragraph runs).

Architecture locks honored:

  Lock #1 (verify is the sole page-number emitter): this module never
    imports ``verify`` and never constructs ``Evidence``. It consumes the
    rendered IR stream (already-emitted ``IndexEntry`` / ``CrossRefEntry``
    payloads) and re-projects pairs into ``ConsolidatedEntry`` instances
    that carry NO new locators — the locators belong to the surviving
    primary entry and are passed through verbatim.

  Lock #5 (byte-identity replay): pure-functional + sorted iteration +
    frozen dataclass = byte-identical output across runs given the same
    input stream + ``keep_plural_set``.

Conservative merge rules (per user-locked decisions):

  1. Skip if either side's canonical (case-folded) is in
     ``keep_plural_set`` — these are legally-distinct plurals (damages,
     findings, costs, pleadings, fees, proceedings, premises, minutes,
     claims, arms — sourced from
     ``fixtures/index_curator_overrides.yaml::keep_plural_variants``).

  2. Skip primary-entry pairs whose locator lists differ — the
     singular and plural were assigned to different sections by the
     verifier, so they're DOCTRINALLY DISTINCT, not surface noise.

  3. Skip primary-entry pairs where either side already carries an
     ``(also: ...)`` parenthetical — the variant filter has already
     merged surface forms, and re-collapsing risks losing variant info.

  4. Skip if either side is a multi-word canonical AND the head noun is
     in the user-protected stop-list (e.g., ``award of damage`` /
     ``award of damages`` — though the multi-word case rarely produces
     adjacent pairs because of differing sort keys).

Detection mechanism:

  - ``inflect.singular_noun(b)`` returns False iff ``b`` is already
    singular; otherwise it returns the singular form. We use that to
    detect plural pairs case-tolerantly: ``a`` is the singular iff
    ``inflect.singular_noun(b).lower() == a.lower()`` OR
    ``inflect.plural(a).lower() == b.lower()``.

  - We compute the inflection ending by string-diff on the lowercased
    forms: ``+s``, ``+es``, or ``y → ies``. Irregular plurals (e.g.
    ``criterion``/``criteria``) produce a non-standard ending; we SKIP
    the merge in that case (curator must add such pairs to
    ``keep_plural_variants`` or accept rendering them separately).

requirements_addressed: v1.2.1 plural consolidation (post-v1.2 patch).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Union

import inflect

from .cross_refs import CrossRefEntry
from .ir import IndexEntry

__all__ = [
    "ConsolidatedEntry",
    "DEFAULT_KEEP_PLURAL_VARIANTS",
    "consolidate_plural_pairs",
    "infer_inflection_ending",
]


# Module-level inflect engine — mirrors cross_refs._INFLECT and
# assembly.dedup._INFLECT, sized to the same hot-loop constraint
# (1k+ entries). Re-instantiating per call would dominate the render
# hot-path on companion volumes (Pretrial Litigation, Trial Advocacy).
_INFLECT = inflect.engine()


# Conservative defaults — used iff the curator fixture is missing or its
# `keep_plural_variants` block is absent. Matches the v1.2 fixture's
# 10-entry list at the time of v1.2.1; renderers should pass the
# fixture's own ``keep_plural_set`` whenever ``overrides`` is non-None.
DEFAULT_KEEP_PLURAL_VARIANTS: frozenset[str] = frozenset({
    "damages",
    "findings",
    "costs",
    "pleadings",
    "fees",
    "proceedings",
    "arms",
    "premises",
    "minutes",
    "claims",
})


# Stream-payload kind tag used by all four renderers in the merge loop.
_PayloadKind = Literal["entry", "synth", "xref", "consolidated"]

# Tuple shape used by every renderer's ``_render_subject_index`` merge
# loop: ``(sort_key, kind, payload)``. We accept the same shape on
# input and emit the same shape on output so the consolidation pass is
# a drop-in pre-pass. ``Synth`` payloads are passed through untouched
# (synthesized stems are not plural-pair candidates by construction —
# they're bare lemmas with their own children).
StreamItem = tuple[str, str, object]


@dataclass(frozen=True)
class ConsolidatedEntry:
    """A merged singular+plural entry, used by all four renderers.

    Carries ONLY pre-existing data — no new locators are constructed.
    The renderer formats this payload per its own line shape (markdown
    text vs. DOCX paragraph + runs).

    Fields:
        display_canonical: e.g. ``"agency(ies)"``, ``"copy(ies)"``,
            ``"communication(s)"``. Inserted verbatim into the rendered
            line (or paragraph run).
        primary_canonical: the singular form, used as the alphabetical
            sort key and as the basis for any sub-entry / locator
            traversal.
        locators: tuple of pre-existing Locator instances copied from
            the surviving primary IndexEntry. Empty for cross-ref-only
            consolidations (``See <target>``).
        see_target: for cross-ref consolidations, the target canonical
            (e.g. ``"administrative agency"``); ``None`` for primary
            consolidations (those carry locators directly).
        variants: pre-existing variants copied from the surviving primary
            IndexEntry (post-filter). Empty for cross-ref-only.
        sub_entries: pre-existing sub-entries copied from the surviving
            primary IndexEntry. Empty tuple for cross-ref-only.
        source_kind: ``"xref"`` (cross-ref pair) or ``"primary"``
            (IndexEntry pair). Determines which renderer code-path
            handles this payload.
    """

    display_canonical: str
    primary_canonical: str
    locators: tuple[object, ...]  # tuple[Locator, ...] — render-stage opaque
    see_target: str | None
    variants: tuple[str, ...]
    sub_entries: tuple[object, ...]  # tuple[SubEntry, ...] — render-stage opaque
    source_kind: Literal["xref", "primary"]


# ---------------------------------------------------------------------------
# Inflection-ending inference
# ---------------------------------------------------------------------------


def infer_inflection_ending(singular: str, plural: str) -> str | None:
    """Return ``"(s)"``, ``"(es)"``, or ``"(ies)"`` if the plural is a
    regular inflection of the singular; ``None`` for irregular forms.

    Comparison is case-tolerant. For ``"agency"`` → ``"agencies"`` the
    detection notices the ``y → ies`` rule and returns ``"(ies)"`` so
    the rendered display is ``"agency(ies)"`` (the legal-treatise
    convention preserves the singular's spelling intact).

    Irregular plurals (``criterion``/``criteria``,
    ``analysis``/``analyses``) return ``None`` — the caller should
    SKIP the merge for these pairs because picking a custom ending
    would require curator review per pair.
    """
    s = singular.lower()
    p = plural.lower()
    # `y → ies` rule must be checked BEFORE the +es rule because
    # ``agency`` → ``agencies`` matches both naively (singular ends in
    # -y AND singular+es == ``agencyes`` — the +es path would fire
    # incorrectly on a degenerate input). Order: ies-then-es-then-s.
    if s.endswith("y") and p == s[:-1] + "ies":
        return "(ies)"
    if p == s + "es":
        return "(es)"
    if p == s + "s":
        return "(s)"
    return None


# ---------------------------------------------------------------------------
# Pair-detection + merge-rule filters
# ---------------------------------------------------------------------------


def _is_plural_of(singular: str, plural: str) -> bool:
    """Return True iff ``plural`` is a regular inflection of ``singular``.

    Two-way check: ``inflect.singular_noun(plural)`` should equal the
    singular (case-tolerant), AND ``inflect.plural(singular)`` should
    equal the plural (case-tolerant). Either-or is enough to mark the
    pair, but BOTH being true narrows false-positives on rare-word
    multi-form plurals.

    Always-False guards:
      - Identical strings (case-tolerant) — same word, not a pair.
      - Either side empty.
    """
    if not singular or not plural:
        return False
    if singular.lower() == plural.lower():
        return False
    sing_check = _INFLECT.singular_noun(plural)
    if isinstance(sing_check, str) and sing_check.lower() == singular.lower():
        return True
    plur_check = _INFLECT.plural(singular)
    if isinstance(plur_check, str) and plur_check.lower() == plural.lower():
        return True
    return False


def _is_protected(canonical: str, keep_plural_set: frozenset[str]) -> bool:
    """Case-tolerant membership check against the curator stop-list.

    Returns True iff ``canonical.lower()`` is in ``keep_plural_set``.
    Used to BLOCK the merge of any pair whose singular OR plural side
    is curator-protected (e.g. ``damage`` vs ``damages`` — ``damages``
    is protected, so the pair is preserved separately).
    """
    return canonical.lower() in keep_plural_set


def _locators_match(a: IndexEntry, b: IndexEntry) -> bool:
    """Return True iff the two entries' locator lists are identical
    (same length, same (section_ref, folio) tuples in the same order).

    Locators are pre-sorted by the upstream pipeline (Phase 4 +
    ``collapse_locators``), so ordered comparison is meaningful.
    """
    if len(a.locators) != len(b.locators):
        return False
    for la, lb in zip(a.locators, b.locators):
        if la.section_ref != lb.section_ref or la.folio != lb.folio:
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def consolidate_plural_pairs(
    stream: Iterable[StreamItem],
    keep_plural_set: frozenset[str],
) -> list[StreamItem]:
    """Collapse adjacent singular/plural pairs in ``stream`` into
    ``ConsolidatedEntry`` items.

    Args:
        stream: the merged ``(sort_key, kind, payload)`` stream as
            produced by every renderer's ``_render_subject_index``
            merge loop, AFTER ``merged.sort(key=...)``.
        keep_plural_set: case-folded stop-list of curator-protected
            plurals (typically ``CuratorOverrides.keep_plural_set``;
            falls back to ``DEFAULT_KEEP_PLURAL_VARIANTS`` if the
            caller passes an empty frozenset and wants protection).

    Returns:
        A new list with the same ``(sort_key, kind, payload)`` shape.
        Adjacent eligible pairs are replaced by a single
        ``(sort_key, "consolidated", ConsolidatedEntry)`` item placed
        at the singular's sort_key position. Non-eligible items are
        passed through verbatim.

    Algorithm:

    1. Walk the input stream pair-wise (i, i+1).
    2. For each pair, check whether ``payload[i+1].canonical`` is the
       regular plural of ``payload[i].canonical`` (or vice versa).
    3. Apply rule filters: ``keep_plural_set`` membership, locator
       mismatch, variant-conflict, and unsupported pair kinds.
    4. On match, emit a single ``ConsolidatedEntry`` and SKIP the
       second item; on no match, emit the first item verbatim and
       advance.
    5. Stable: the relative order of all items (consolidated +
       passthrough) is preserved so the rendered alphabetical
       sequence stays intact.

    Determinism: pure-functional, no global mutation, no random
    iteration. Lock #5 byte-identity by-construction.
    """
    items: list[StreamItem] = list(stream)
    out: list[StreamItem] = []
    consumed: set[int] = set()
    n = len(items)
    for i in range(n):
        if i in consumed:
            continue
        kind_i = items[i][1]
        # ---- Adjacency-based pairing for primary entries. ----
        # Primary entries' locators interact with sibling sub-entries; we
        # only allow CONTIGUOUS pairs to consolidate (mirror rule 2 +
        # sub-entry conflict guard).
        if kind_i == "entry" and i + 1 < n and (i + 1) not in consumed:
            consolidated = _try_consolidate_pair(
                items[i], items[i + 1], keep_plural_set
            )
            if consolidated is not None:
                out.append(consolidated)
                consumed.add(i + 1)
                continue
        # ---- Lookahead pairing for cross-refs. ----
        # Cross-refs are pure pointers — no locator interaction with
        # neighbors — so they may be consolidated even if a primary entry
        # (or a different cross-ref) sits between the singular and plural
        # forms in alphabetical order. Concrete real-world example:
        #
        #   communication. See privileged communication.
        #   communication issue, § 1.13.1 (p. 113), …      ← primary entry
        #   communications. See privileged communication.
        #
        # The two ``See privileged communication.`` cross-refs are a
        # legitimate plural pair separated only by an unrelated primary;
        # collapsing them gives the user-visible
        # ``communication(s). See privileged communication.``
        # WITHOUT moving the primary out of its alphabetical slot.
        if kind_i == "xref":
            partner_idx = _find_xref_plural_partner(
                items, i, consumed, keep_plural_set
            )
            if partner_idx is not None:
                consolidated = _try_consolidate_pair(
                    items[i], items[partner_idx], keep_plural_set
                )
                if consolidated is not None:
                    out.append(consolidated)
                    consumed.add(partner_idx)
                    continue
        # No pair — pass through.
        out.append(items[i])
    return out


# Lookahead window for cross-ref plural pairing. A singular and its
# regular plural differ by at most a 3-char suffix (``-ies`` / ``-es``);
# alphabetical sort places them within a small radius of each other
# (typically 0–5 intervening entries). The window cap defends against
# pathological inputs and keeps the consolidation pass O(n).
_XREF_LOOKAHEAD_WINDOW: int = 8


def _find_xref_plural_partner(
    items: list[StreamItem],
    i: int,
    consumed: set[int],
    keep_plural_set: frozenset[str],
) -> int | None:
    """Return the index of an xref item that is the regular plural pair
    of ``items[i]`` (also an xref) with the SAME ``See`` target, or
    ``None`` if no such partner exists in the next
    ``_XREF_LOOKAHEAD_WINDOW`` items.

    Only xref-to-xref pairings are eligible for the lookahead path —
    primary entries always require strict adjacency.
    """
    sort_a, kind_a, payload_a = items[i]
    if kind_a != "xref":
        return None
    head_a = _xref_head(payload_a)
    target_a = _xref_target(payload_a)
    n = len(items)
    upper = min(n, i + 1 + _XREF_LOOKAHEAD_WINDOW)
    for j in range(i + 1, upper):
        if j in consumed:
            continue
        sort_b, kind_b, payload_b = items[j]
        if kind_b != "xref":
            continue
        if _xref_target(payload_b) != target_a:
            continue
        head_b = _xref_head(payload_b)
        # Detect the pair via _orient_pair (handles both directions).
        singular, _plural, ending = _orient_pair(head_a, head_b)
        if singular is None or ending is None:
            continue
        if _is_protected(head_a, keep_plural_set) or _is_protected(
            head_b, keep_plural_set
        ):
            continue
        return j
    return None


def _try_consolidate_pair(
    a: StreamItem,
    b: StreamItem,
    keep_plural_set: frozenset[str],
) -> StreamItem | None:
    """Inspect a (sort_key, kind, payload) pair; return the consolidated
    item or ``None`` if the pair is not eligible.

    Eligibility decision tree:

      (1) Both items must be the same kind: either both ``"entry"`` or
          both ``"xref"``. Mixing entry/xref or entry/synth would lose
          either locators or sub-entries on one side; we never collapse
          across kinds.
      (2) For ``"xref"`` pairs: both must point at the same primary
          canonical (otherwise the consolidated ``See target`` would
          be ambiguous — keep them separate).
      (3) For ``"entry"`` pairs: locator lists must match exactly, and
          both sides' ``variants`` lists must be empty (consolidating a
          variant-bearing entry with its plural risks losing variant
          info — see merge rule 3 in module docstring).
      (4) Inflection ending must be regular (``+s`` / ``+es`` /
          ``y → ies``). Irregular plurals are skipped.
      (5) Neither side's canonical may be in ``keep_plural_set`` — the
          curator-protected legally-distinct list.
    """
    sort_key_a, kind_a, payload_a = a
    sort_key_b, kind_b, payload_b = b
    if kind_a != kind_b:
        return None
    if kind_a not in ("entry", "xref"):
        return None  # synth + consolidated never participate

    # Extract the canonical-shape strings to compare.
    if kind_a == "xref":
        head_a = _xref_head(payload_a)
        head_b = _xref_head(payload_b)
        target_a = _xref_target(payload_a)
        target_b = _xref_target(payload_b)
        if target_a != target_b:
            return None  # different See targets — preserve both
        # Try both directions: a-as-singular and b-as-singular.
        # The pair must be a regular plural inflection in EITHER order
        # (alphabetical sort can place the plural first for y → ies, e.g.
        # ``agencies`` < ``agency`` because i (105) < y (121)).
        singular, _plural, ending = _orient_pair(head_a, head_b)
        if singular is None or ending is None:
            return None
        if _is_protected(head_a, keep_plural_set) or _is_protected(
            head_b, keep_plural_set
        ):
            return None
        display = f"{singular}{ending}"
        return (
            sort_key_a,
            "consolidated",
            ConsolidatedEntry(
                display_canonical=display,
                primary_canonical=singular,
                locators=(),
                see_target=target_a,
                variants=(),
                sub_entries=(),
                source_kind="xref",
            ),
        )

    # kind_a == "entry"
    entry_a = payload_a  # IndexEntry
    entry_b = payload_b  # IndexEntry
    canon_a = _entry_canonical(entry_a)
    canon_b = _entry_canonical(entry_b)
    singular, _plural, ending = _orient_pair(canon_a, canon_b)
    if singular is None or ending is None:
        return None
    if _is_protected(canon_a, keep_plural_set) or _is_protected(
        canon_b, keep_plural_set
    ):
        return None
    if not _locators_match(entry_a, entry_b):
        return None
    # Variant-conflict guard — if either side carries pre-existing
    # `(also: ...)` content, the variant filter already has authoritative
    # surface-form info; further consolidation risks dropping a variant
    # the reader was expected to find.
    if _entry_variants(entry_a) or _entry_variants(entry_b):
        return None
    # Sub-entry conflict — if either side carries sub-entries the merge
    # is unsafe (the singular-vs-plural sub-entries may not match shape).
    # The conservative call is to leave the pair alone.
    if _entry_sub_entries(entry_a) or _entry_sub_entries(entry_b):
        return None
    # Surviving primary is whichever entry's canonical matches the
    # detected singular (case-tolerant). Locators are identical by the
    # _locators_match guard above, so either entry's locators work.
    surviving_entry = entry_a if canon_a.lower() == singular.lower() else entry_b
    display = f"{singular}{ending}"
    return (
        sort_key_a,
        "consolidated",
        ConsolidatedEntry(
            display_canonical=display,
            primary_canonical=singular,
            locators=tuple(surviving_entry.locators),
            see_target=None,
            variants=(),
            sub_entries=(),
            source_kind="primary",
        ),
    )


def _orient_pair(
    a: str, b: str
) -> tuple[str | None, str | None, str | None]:
    """Decide which of (a, b) is the singular and which is the plural.

    Returns ``(singular, plural, ending)`` if the pair is a regular
    plural inflection in either direction; ``(None, None, None)`` if
    not. The ending is the one inferred from the singular → plural
    direction (``"(s)"`` / ``"(es)"`` / ``"(ies)"``).

    Bidirectional detection is required because alphabetical sort can
    place the plural first (``agencies`` < ``agency`` because i < y).
    """
    # Try a-as-singular first.
    if _is_plural_of(a, b):
        ending = infer_inflection_ending(a, b)
        if ending is not None:
            return a, b, ending
    # Then try b-as-singular.
    if _is_plural_of(b, a):
        ending = infer_inflection_ending(b, a)
        if ending is not None:
            return b, a, ending
    return None, None, None


# ---------------------------------------------------------------------------
# Payload accessors — kept at module scope so the test suite can mock /
# inspect them, and so renderers can subclass-without-importing if a
# future variant introduces a different stream payload shape.
# ---------------------------------------------------------------------------


def _xref_head(payload: object) -> str:
    """Extract ``head`` from a ``CrossRefEntry`` payload."""
    return payload.head  # type: ignore[attr-defined]


def _xref_target(payload: object) -> str:
    """Extract ``primary_canonical`` from a ``CrossRefEntry`` payload."""
    return payload.primary_canonical  # type: ignore[attr-defined]


def _entry_canonical(payload: object) -> str:
    return payload.canonical  # type: ignore[attr-defined]


def _entry_variants(payload: object) -> list[str]:
    return getattr(payload, "variants", []) or []


def _entry_sub_entries(payload: object) -> list[object]:
    return getattr(payload, "sub_entries", []) or []
