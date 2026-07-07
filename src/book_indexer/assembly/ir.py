"""Pydantic IR (intermediate representation) for Phase 4 — Canonicalization
& Index Assembly.

Locked per RESEARCH §H-10 + D-07. Every model is ``frozen=True`` +
``extra='forbid'`` so schema drift is a ValidationError (not silent
corruption). Mirrors the Phase 3b ``tables/ir.py`` style.

``Locator`` is REUSED from ``book_indexer.tables.ir`` — single source
of truth per D-07. Do NOT redefine it here. Wave 1 dedup, Wave 2 sweep,
Wave 2 cite-rule, and Wave 3 tree-builder all consume the same Locator
class so locator equality and JSON shape are byte-identical across phases.

Round-trip contract: ``model.model_dump(mode="json")`` then
``Cls.model_validate(payload)`` must produce an equal instance — locks
JSON serialization shape for the byte-identical replay invariant
(Lock #5).

Public exports (re-exported via ``book_indexer.assembly``):
    SubEntry, IndexEntry, IndexTreeProvenance, IndexTree.

requirements_addressed: ASM-01 (canonical-form selection — IndexEntry.canonical
is the chosen lemma representative), ASM-08 (frozen Pydantic IR for the
IndexTree), and indirectly ASM-04..ASM-07 (downstream stages target this IR).
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Single source of truth for Locator. DO NOT redefine — Phase 2 evidence
# contract + Phase 3b tables/ir.py + Phase 4 assembly/ir.py must all use
# the SAME Locator class so locator equality and JSON shape are byte-
# identical across the pipeline (Lock #5 / D-07).
from book_indexer.tables.ir import Locator


# Slug regex for IndexEntry.id: lowercase ASCII, optional hyphens, optional
# numeric collision suffix (-2, -3, ...) per D-07 + RESEARCH §H-10.
# Examples — accept: ``voir-dire``, ``voir-dire-2``, ``frcp-12``,
# ``rule-of-completeness``. Reject: ``Voir-dire`` (uppercase), ``-voir``
# (leading hyphen), ``2-things`` (leading digit allowed by [a-z0-9] but the
# trailing collision suffix grammar requires hyphen+digits, not digits alone).
_INDEX_ENTRY_ID_PATTERN = r"^[a-z0-9][-a-z0-9]*(-\d+)?$"


class SubEntry(BaseModel):
    """A single sub-entry under an IndexEntry parent (D-04, depth 1 only).

    Sub-entries are produced by ``subdivide.py`` when a parent has >7
    locators and the LLM proposed sub-bucket labels. Each sub-entry carries
    its own ``locators`` list (a strict subset of the parent's locators).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    text: Annotated[str, Field(min_length=1)]
    sort_key: Annotated[str, Field(min_length=1)]
    locators: list[Locator]


class IndexEntry(BaseModel):
    """A single parent entry in the IndexTree.

    ``id`` is the slugified canonical form with optional numeric collision
    suffix (matches ``_INDEX_ENTRY_ID_PATTERN``).

    ``canonical`` is the display form chosen by D-01 (longest spelled-out
    form when an acronym ↔ spelled-out merge happened; lemma representative
    otherwise).

    ``derived_from_table`` is set ONLY when the entry was promoted from one
    of the Phase 3b sidecar tables (cases / statutes / rules); ``None`` for
    LLM-discovered concepts.

    ``locators`` is the verified-citation list emitted by ``verify()`` for
    this concept's appearances. Empty list is structurally permitted (the
    zero-evidence-drop logic in Wave 2 emits zero-locator buckets transiently
    before drop), but the final IndexTree NEVER ships zero-locator entries —
    enforced by a post-build invariant test in Plan 04-03.

    ``sub_entries`` is depth-1 only (no nesting deeper than this — D-04).
    ``see`` and ``see_also`` are lists of OTHER IndexEntry.id slugs (D-08).
    ``variants`` is the merged-acronym + lemma-variant surface forms.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    id: Annotated[str, Field(pattern=_INDEX_ENTRY_ID_PATTERN)]
    canonical: Annotated[str, Field(min_length=1)]
    sort_key: Annotated[str, Field(min_length=1)]
    derived_from_table: Literal["cases", "statutes", "rules"] | None = None
    locators: list[Locator]
    sub_entries: list[SubEntry] = []
    see: list[str] = []
    see_also: list[str] = []
    variants: list[str] = []


class IndexTreeProvenance(BaseModel):
    """Single sidecar carrying upstream version metadata + assembly counts.

    ``frozen_timestamp`` is typed ``Literal[0]`` (default 0) so any non-zero
    leak fails validation immediately — defense-in-depth for Lock #5
    byte-identity. Mirrors the Phase 3b ``TableProvenance`` pattern.

    Fields:
      ``concepts_sha`` / ``tables_sha`` — per-input content hashes (the
      Phase 3a + Phase 3b artifacts consumed). dict ordering is locked at
      JSON-emit time (orjson OPT_SORT_KEYS in callers).

      ``pre_dedup_count`` … ``post_zero_evidence_count`` — entry counts
      after each pipeline stage so the operator can audit attrition.

      ``oversize_parent_count`` / ``sub_entry_total_count`` /
      ``max_sub_entries_per_parent`` — D-04 subdivision metrics.

      ``oob_status`` — Out-of-band status: ``"under"`` if the final tree
      has <800 entries, ``"over"`` if >1500, ``"none"`` otherwise.

      ``parents_with_no_locators`` — should always be 0 in shipping trees;
      non-zero indicates a bug in the zero-evidence drop pass.

      ``dropped_table_citations`` / ``zero_evidence_drops`` — diagnostic
      lists of dropped entries (operator review).

      ``slug_collision_count`` — number of ``-2`` / ``-3`` suffixes assigned
      during slug deconfliction.

      ``iteration_depth`` — actual subdivision iteration depth used (≤2).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    spacy_version: Annotated[str, Field(min_length=1)]
    spacy_model_sha: Annotated[str, Field(min_length=1)]
    eyecite_version: Annotated[str, Field(min_length=1)]
    reporters_db_version: Annotated[str, Field(min_length=1)]
    courts_db_version: Annotated[str, Field(min_length=1)]
    pdf_sha256: Annotated[str, Field(min_length=1)]
    corpus_sha: Annotated[str, Field(min_length=1)]
    concepts_sha: dict[str, str]
    tables_sha: dict[str, str]
    pre_dedup_count: int
    post_dedup_count: int
    post_deconflict_count: int
    post_zero_evidence_count: int
    oversize_parent_count: int
    sub_entry_total_count: int
    oob_status: Literal["none", "under", "over"]
    max_sub_entries_per_parent: int
    parents_with_no_locators: int
    dropped_table_citations: list[dict]
    zero_evidence_drops: list[str]
    slug_collision_count: int
    iteration_depth: int
    frozen_timestamp: Literal[0] = 0


class IndexTree(BaseModel):
    """Top-level envelope for ``artifacts/index_tree.json``.

    ``schema_version`` is bumped any time IR shape changes (currently "1").
    ``entries`` is sorted alphabetically by ``sort_key`` by the producer
    (D-01); the IR does NOT auto-sort. ``provenance`` is the sidecar
    written to ``artifacts/index_tree.provenance.json``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: Annotated[str, Field(min_length=1)]
    provenance: IndexTreeProvenance
    entries: list[IndexEntry]
