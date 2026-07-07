"""Pydantic IR (intermediate representation) for Phase 3b — Tables of Cases,
Statutes, and Rules.

Locked per RESEARCH §H-12. Every model is ``frozen=True`` + ``extra='forbid'``
so schema drift is a ValidationError (not silent corruption). Mirrors the
Phase 2 ``Evidence`` style.

Round-trip contract: ``model.model_dump(mode="json")`` then
``Cls.model_validate(payload)`` must produce an equal instance — locks JSON
serialization shape for the byte-identical replay invariant (Lock #5).

Public exports (re-exported via ``book_indexer.tables``):
    Locator, CaseEntry, StatuteEntry, RuleEntry, SubsectionEntry,
    TableProvenance, TableOfCases, TableOfStatutes, TableOfRules.

requirements_addressed: TAB-01 (sort_key field), TAB-02 (StatuteEntry),
TAB-03 (RuleEntry + SubsectionEntry), TAB-04 (Locator.evidence_id FK).
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Section-ref regex: copied byte-for-byte from src/book_indexer/verify/evidence.py
# so Locator.section_ref matches the Phase 2 contract verbatim. DO NOT redefine
# independently — drift between this pattern and Evidence._SECTION_REF_PATTERN
# is a ship-blocker.
_SECTION_REF_PATTERN = r"^§\d+(\.\d{2}(\.\d+)?)?$"


# Allowed rule systems for RuleEntry. MRPC included up-front (per Plan 03B-01
# task 2 implementation note) so adding it later is not a breaking IR change.
# FedR / Rule remain for future extensibility (state-rule abbreviations and
# unspecified bare-rule routing per D-06).
RuleSystem = Literal["FRE", "FRCP", "FRAP", "FedR", "Rule", "MRPC"]


class Locator(BaseModel):
    """A single (section_ref, folio) pair joined to the evidence ledger.

    ``evidence_id`` is the FK to the row written by ``verify()`` for this
    citation appearance — TAB-04 invariant: every Locator must have a
    matching evidence row (enforced by Plan 04 invariant test).

    ``section_ref`` MUST match Phase 2 ``Evidence.section_ref`` regex
    verbatim (``^§\\d+(\\.\\d{2}(\\.\\d+)?)?$``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    section_ref: Annotated[str, Field(pattern=_SECTION_REF_PATTERN)]
    folio: str
    evidence_id: Annotated[int, Field(ge=1)]


class CaseEntry(BaseModel):
    """A single case row in the Table of Cases.

    ``display_name`` is the rendered form (no prefix stripping); ``sort_key``
    is the D-01 alphabetization key (computed by
    ``book_indexer.tables.alphabetize.sort_key``).

    ``locators`` is provided by the producer in ascending section_ref order
    (D-03). The model does NOT auto-sort — the IR is a faithful mirror of
    the producer's decisions, locking determinism upstream.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    display_name: Annotated[str, Field(min_length=1)]
    sort_key: Annotated[str, Field(min_length=1)]
    canonical_citation: Annotated[str, Field(min_length=1)]
    reporter: str
    court: str | None = None
    year: int | None = None
    locators: list[Locator]


class StatuteEntry(BaseModel):
    """A single statute row in the Table of Statutes.

    ``display_name`` is the canonical eyecite-normalized rendering. ``title``
    and ``section`` are decomposed for sort key computation
    (e.g., ``42 U.S.C. § 1983`` → title=``42``, section=``1983``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    display_name: Annotated[str, Field(min_length=1)]
    sort_key: Annotated[str, Field(min_length=1)]
    canonical_citation: Annotated[str, Field(min_length=1)]
    title: str
    section: str
    publisher: str | None = None
    locators: list[Locator]


class SubsectionEntry(BaseModel):
    """A single sub-row under a parent RuleEntry (D-05, depth 1 only).

    ``subsection_path`` is the parenthetical path (e.g., ``(b)``, ``(b)(1)``).
    Sorted lexicographically on ``subsection_path`` by the producer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    subsection_path: Annotated[str, Field(min_length=1)]
    locators: list[Locator]


class RuleEntry(BaseModel):
    """A single parent-rule row in the Table of Rules (D-05).

    ``parent_rule`` is the bare rule name (e.g., ``FRE 404``). ``rule_system``
    is the canonical system code. ``parent_locators`` carries appearances of
    the bare parent (no parenthetical); ``subsections`` carries every
    (subsection_path, locators) sub-row at depth 1.

    Sub-entries are sorted lex on ``subsection_path`` by the producer. The IR
    does NOT auto-sort.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    parent_rule: Annotated[str, Field(min_length=1)]
    rule_system: RuleSystem
    sort_key: Annotated[str, Field(min_length=1)]
    parent_locators: list[Locator]
    subsections: list[SubsectionEntry]


class TableProvenance(BaseModel):
    """Single sidecar carrying upstream version metadata + counts.

    ``frozen_timestamp`` is typed ``Literal[0]`` (default 0) so any non-zero
    leak fails validation immediately — defense-in-depth for Lock #5
    byte-identity. JSON serialization sorts dict keys (orjson OPT_SORT_KEYS
    in callers); ``chapter_rule_systems`` therefore replays byte-identical.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    eyecite_version: Annotated[str, Field(min_length=1)]
    reporters_db_version: Annotated[str, Field(min_length=1)]
    courts_db_version: Annotated[str, Field(min_length=1)]
    pdf_sha256: Annotated[str, Field(min_length=1)]
    corpus_sha: Annotated[str, Field(min_length=1)]
    jurisdictions_enabled: list[str]
    chapter_rule_systems: dict[str, str]
    cite_counts: dict[str, int]
    regex_fallback_counts: dict[str, int]
    unresolved_short_cites: list[dict]
    unverified_extractions: list[dict]
    frozen_timestamp: Literal[0] = 0


class TableOfCases(BaseModel):
    """Top-level envelope for ``artifacts/tables/cases.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: Annotated[str, Field(min_length=1)]
    entries: list[CaseEntry]
    provenance: TableProvenance


class TableOfStatutes(BaseModel):
    """Top-level envelope for ``artifacts/tables/statutes.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: Annotated[str, Field(min_length=1)]
    entries: list[StatuteEntry]
    provenance: TableProvenance


class TableOfRules(BaseModel):
    """Top-level envelope for ``artifacts/tables/rules.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: Annotated[str, Field(min_length=1)]
    entries: list[RuleEntry]
    provenance: TableProvenance
