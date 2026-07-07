"""Phase 5 IR contract layer.

Re-imports IndexTree/IndexEntry/SubEntry from book_indexer.assembly
and Locator from book_indexer.tables.ir — single source of truth
per D-07. Defines two NEW frozen dataclasses Phase 5 owns:
SyntheticEntry (B-06 render-time projection) and FormattedLocator
(D-03 collapsed locator string).

The dataclasses are NOT Pydantic — they're in-memory render-stage
intermediates. The shipped IR (artifacts/index_tree.json) is
unchanged; synthetic entries appear ONLY in the rendered MD/DOCX
output and in coverage.md (per Open Question 3 — RESEARCH §H-12
recommendation).

requirements_addressed: implicit D-03 (range_collapse output),
    implicit D-04 (B-06 synthesize output).
"""
from __future__ import annotations

from dataclasses import dataclass

# Re-imports — DO NOT redefine. Phase 5 is a read-only consumer of
# Phase 4 IR and Phase 3b table IR.
from book_indexer.assembly import (
    IndexEntry,
    IndexTree,
    IndexTreeProvenance,
    SubEntry,
)
from book_indexer.tables.ir import Locator

__all__ = [
    "FormattedLocator",
    "IndexEntry",
    "IndexTree",
    "IndexTreeProvenance",
    "Locator",
    "SubEntry",
    "SyntheticEntry",
]


@dataclass(frozen=True)
class SyntheticEntry:
    """B-06 render-time projection of a bare-stem main entry.

    Has NO corresponding row in IndexTree.entries — synthesized at
    render time by render/synthesize.py from sibling IndexEntry
    canonicals whose token-lemma sets contain a common stem (e.g.
    'hearsay' from {'admissible hearsay', 'hearsay exception',
    'hearsay statement', 'inadmissible hearsay'}).

    Per RESEARCH §H-5 the algorithm is union-of-token-lemmas (NOT
    first-token-lemma per CONTEXT D-04 — refined empirically to
    catch 'hearsay' which the first-token approach misses).

    Fields:
        stem: bare lemma (length >= 4); the displayed canonical for
            the synthetic main entry.
        sibling_canonicals: tuple of IndexEntry.canonical values that
            contain this stem in their token-lemma sets.
        locators: union of all sibling locators, deduped by
            (section_ref, folio).
    """

    stem: str
    sibling_canonicals: tuple[str, ...]
    locators: tuple[Locator, ...]


@dataclass(frozen=True)
class FormattedLocator:
    """D-03 page-range collapse output (single rendered locator string).

    range_collapse.collapse_locators() returns list[FormattedLocator];
    markdown.py + docx.py read ``rendered`` directly. The
    ``is_range`` flag distinguishes singular ``(p. N)`` from plural
    ``(pp. N–M)`` for downstream styling. ``evidence_ids`` carries
    every underlying Evidence row's id for audit join (AUD-01).

    Per RESEARCH §H-6, D-03 is VACUOUSLY EXERCISED on the reference corpus
    (Phase 4 cite-rule already coalesces multi-folio occurrences to
    one Locator per section_ref). Forward-compat for companion
    volumes — Pretrial Litigation, Trial Advocacy.
    """

    section_ref: str
    rendered: str
    is_range: bool
    evidence_ids: tuple[int, ...]
