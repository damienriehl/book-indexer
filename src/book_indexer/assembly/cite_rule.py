"""Hybrid deepest-containing-section rule per D-02 + ASM-03.

Pure function over ``list[Evidence]``; emits ``list[Locator]``. The 4
standard ASM-03 cases (sub-section / sibling-promote / major-promote /
cross-chapter-split) plus D-02's singleton-at-depth + lopsided-cross-chapter
refinements are encoded in :func:`lowest_common_ancestor` and
:func:`cite_for_canonical`.

Per CONTEXT.md D-02 + RESEARCH §P-6: Phase 1's section_id assignment is
trusted verbatim. Misclassified sections (B-03) become acceptable v1.0
noise; this module does NOT re-run section detection.

Architecture Lock #1: this module never constructs ``Evidence`` directly.
It only reads ``ev.section_path``, ``ev.folio``, ``ev.pdf_page``, and
``ev.token_offset`` to build ``Locator`` objects. The Locator's
``evidence_id`` is filled with the placeholder ``0``; Plan 04-04
``tree.py`` will replace with the actual evidence_id at ledger-emit time
(Evidence carries no evidence_id field in the Phase 2 contract — it's
assigned at ledger-write boundary).

requirements_addressed: ASM-03 (hybrid deepest-containing-section rule).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from book_indexer.tables.ir import Locator
from book_indexer.verify.evidence import Evidence

# Plan 04-04 ``tree.py`` replaces this sentinel with the actual
# evidence_id once the evidence ledger is emitted. The plan text
# described ``evidence_id=0`` but ``Locator.evidence_id`` is
# IR-constrained ``ge=1``, so the lowest-legal placeholder is 1.
# Recorded as a deviation (Rule 1 — IR validator would reject 0).
_PLACEHOLDER_EVIDENCE_ID = 1


def lowest_common_ancestor(paths: list[tuple[str, ...]]) -> str:
    """Return the deepest section_ref present in all paths.

    Behavior:
      * Empty input → ``""`` (defensive).
      * Singleton path → its own deepest element (D-02: no auto-promote).
      * Multiple paths → deepest common prefix; ``zip`` truncates to the
        shortest path length so a mix of (§2,§2.04,§2.04.5) +
        (§2,§2.04) yields ``§2.04`` (RESEARCH §H-3 case 8).
      * No common element at all → first path's chapter (defensive
        fallback for cross-chapter inputs that should never reach this
        helper because :func:`cite_for_canonical` groups by chapter
        first).
    """
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0][-1]
    common: list[str] = []
    for refs_at_depth in zip(*paths):
        if all(r == refs_at_depth[0] for r in refs_at_depth):
            common.append(refs_at_depth[0])
        else:
            break
    return common[-1] if common else paths[0][0]


def group_by_chapter(evidence_list: Iterable[Evidence]) -> dict[int, list[Evidence]]:
    """Group ``Evidence`` rows by chapter integer (parsed from section_path[0]).

    ``section_path[0]`` is the level-1 chapter ref (e.g., ``"§2"``); strip
    the ``§`` prefix and parse to ``int``. Defensive fallback: anything
    without a parseable chapter goes to bucket ``0`` (Phase 1 enforces
    section_path shape so this should be unreachable in production).
    """
    groups: dict[int, list[Evidence]] = defaultdict(list)
    for ev in evidence_list:
        try:
            chap = int(ev.section_path[0].lstrip("§"))
        except (IndexError, ValueError):
            chap = 0
        groups[chap].append(ev)
    return dict(groups)


def cite_for_canonical(evidence_list: list[Evidence]) -> list[Locator]:
    """Hybrid deepest-containing-section rule per D-02 + ASM-03.

    Algorithm:
      1. Group ``Evidence`` rows by chapter (one cluster per chapter,
         ASM-03 case d).
      2. Within each chapter cluster, find the deepest common ancestor
         of all ``section_path`` tuples.
      3. Pick the ``min(pdf_page, token_offset)`` representative within
         the cluster for ``folio`` assignment (deterministic — same
         input always yields same Locator).
      4. Emit one ``Locator`` per chapter; sort output by chapter
         ascending.

    Returns:
        list[Locator] — one per chapter cluster, sorted by chapter.
        ``evidence_id`` is the placeholder ``_PLACEHOLDER_EVIDENCE_ID``;
        Plan 04-04 ``tree.py`` replaces with the actual evidence_id
        after the ledger is emitted.
    """
    if not evidence_list:
        return []

    by_chapter = group_by_chapter(evidence_list)
    locators: list[Locator] = []

    for chap in sorted(by_chapter.keys()):
        evs = by_chapter[chap]
        paths = [ev.section_path for ev in evs]
        deepest = lowest_common_ancestor(paths)
        if not deepest:
            # Defensive: shouldn't happen because group_by_chapter
            # already partitions, and within-chapter LCA always finds
            # at least the chapter itself.
            continue
        rep = min(evs, key=lambda e: (e.pdf_page, e.token_offset))
        locators.append(
            Locator(
                section_ref=deepest,
                folio=rep.folio,
                evidence_id=_PLACEHOLDER_EVIDENCE_ID,
            )
        )

    return locators
