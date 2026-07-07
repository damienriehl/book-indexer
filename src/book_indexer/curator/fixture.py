"""Curator fixture Pydantic models + YAML loader + JSON-Schema generators.

Per Phase 7 CONTEXT D-07/D-08/D-13:

- ``CuratorOverrides`` — top-level YAML container. Validates the
  ``fixtures/index_curator_overrides.yaml`` shape and enforces the curator
  gate (``metadata.curated_by != "PENDING_AUTHOR"``).
- ``RemovalsResponse`` / ``RecapitalizationsResponse`` — LLM-output schemas.
  Pydantic ``extra="forbid"`` blocks any locator-shaped field (Architecture
  Lock #2; ship-blocker test ``test_curator_schemas_locator_free.py``).
- ``RecapitalizationRecord`` — runs strict guard at validation time
  (``wrong.lower() == right.lower()``) so malformed pairs hard-fail before
  any text is mutated.
- ``CuratorOverrides.keep_plural_variants`` — CUR-03 (D-13) author-curated
  exclusion list of canonicals whose plural variants MUST be preserved
  (legal-domain-distinct: ``damages``, ``findings``, etc.).

Lock #2 confirmation: only ``RemovalsResponse`` and ``RecapitalizationsResponse``
are consumed by the LLM (passed as ``--json-schema`` to ``claude -p``);
``CuratorOverrides`` is a curator-only schema (YAML loaded by the human's
fixture, never returned by the LLM) and therefore is NOT gated by Lock #2.

requirements_addressed: CUR-01, CUR-02, CUR-03.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .errors import CuratorFixtureError

__all__ = [
    "CoverageAuditEntry",
    "CuratorFixtureError",
    "CuratorOverrides",
    "DropClassificationResponse",
    "DropEntry",
    "EditorialOverrides",
    "R1StripVariantsRule",
    "R2RecapitalizeRule",
    "R3RewordRule",
    "R4DeleteEntryRule",
    "R5DeleteXrefRule",
    "R6PromoteSingleChildRule",
    "R7FoldDoubledWordRule",
    "R8PluralCanonicalRule",
    "R9WhitespaceRule",
    "RecapitalizationRecord",
    "RecapitalizationsResponse",
    "RecoveredTerm",
    "RecoveredTermsSpotCheck",
    "RemovalRecord",
    "RemovalsResponse",
    "SpotCheckRecord",
    "ZeroEvidenceDropsClassification",
    "build_coverage_audit_schema",
    "build_drop_classifications_schema",
    "build_recapitalizations_schema",
    "build_removals_schema",
    "load_curator_overrides",
    "load_editorial_overrides",
    "load_recovered_terms_spot_check",
    "load_zero_evidence_drops_classification",
]


# ---------------------------------------------------------------------------
# LLM proposal schemas (Lock #2 — extra="forbid", zero locator-shaped fields)
# ---------------------------------------------------------------------------


class RemovalRecord(BaseModel):
    """One removal proposal: a term + reason. Locator-free by construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    term: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RemovalsResponse(BaseModel):
    """LLM output for ``propose_removals.py``. Wraps a list of RemovalRecord."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    removals: list[RemovalRecord] = Field(default_factory=list)


class RecapitalizationRecord(BaseModel):
    """One capitalization-fix proposal: ``(wrong, right, reason)``.

    Strict guard runs at validation time: ``wrong.lower() == right.lower()``.
    Any letter delta raises ``ValidationError`` with ``strict_guard_violation``
    in the message — caught at LLM-output parse time, before any text is
    transformed downstream.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    wrong: str = Field(min_length=1)
    right: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _strict_guard(self) -> RecapitalizationRecord:
        if self.wrong.lower() != self.right.lower():
            raise ValueError(
                f"strict_guard_violation: letters changed between "
                f"{self.wrong!r} and {self.right!r} "
                f"(lower forms differ: {self.wrong.lower()!r} != "
                f"{self.right.lower()!r}). "
                f"CUR-02 only allows case mutation."
            )
        return self


class RecapitalizationsResponse(BaseModel):
    """LLM output for ``propose_capitalizations.py``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recapitalizations: list[RecapitalizationRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Curator-only schemas (NOT consumed by LLM — only the YAML fixture)
# ---------------------------------------------------------------------------


class _ExpectedEntryCountBand(BaseModel):
    """Wave 5 cold-build acceptance band; placeholder ``{0, 0}`` until locked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min: int = Field(ge=0)
    max: int = Field(ge=0)


class _MetadataBlock(BaseModel):
    """Curator fixture metadata + signoff gate."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: int = Field(ge=1)
    curated_by: str = Field(min_length=1)
    curated_at_iso: str = Field(default="")
    source_index_version: str = Field(min_length=1)
    source_index_sha256: str = Field(default="")
    expected_entry_count_band: _ExpectedEntryCountBand

    @field_validator("curated_by")
    @classmethod
    def _reject_pending_author(cls, v: str) -> str:
        if v.strip() == "PENDING_AUTHOR":
            raise ValueError(
                "PENDING_AUTHOR sentinel: fixture is unsigned. The author MUST "
                "set metadata.curated_by to their email + curated_at_iso to a "
                "real ISO 8601 UTC timestamp before the build proceeds. "
                "(Phase 7 CONTEXT D-08 curator gate.)"
            )
        return v


class SpotCheckRecord(BaseModel):
    """B-14 author-verified ``(term, section_ref, folio)`` triple."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    term: str = Field(min_length=1)
    section_ref: str = Field(min_length=1)
    folio: str = Field(min_length=1)
    verified: bool
    checked_against_pdf: bool
    note: str = Field(default="")


class CuratorOverrides(BaseModel):
    """Top-level curator fixture container — validates the entire YAML.

    Loading flow: ``load_curator_overrides(path)`` → ``yaml.safe_load`` →
    ``CuratorOverrides.model_validate``. The metadata curator-gate check
    runs inside ``_MetadataBlock._reject_pending_author`` (raises
    ``ValueError`` → ``ValidationError``); ``load_curator_overrides``
    re-raises as ``CuratorFixtureError`` for caller convenience.

    CUR-03 (D-13): ``keep_plural_variants`` is the author-curated exclusion
    list of canonicals whose plural variants must be preserved. Default
    empty (backward-compatible with v1.0 fixtures that lack the block).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    metadata: _MetadataBlock
    remove: list[RemovalRecord] = Field(default_factory=list)
    recapitalize: list[RecapitalizationRecord] = Field(default_factory=list)
    spot_check: list[SpotCheckRecord] = Field(default_factory=list)
    keep_plural_variants: list[str] = Field(default_factory=list)

    # ---- Computed-property accessors (frozen models — define as @property) ----

    @property
    def removal_set(self) -> frozenset[str]:
        """All ``remove[].term`` as a frozenset for O(1) lookup."""
        return frozenset(r.term for r in self.remove)

    @property
    def recapitalize_pairs(self) -> tuple[tuple[str, str], ...]:
        """Sequential ``(wrong, right)`` pairs in YAML-declaration order."""
        return tuple((r.wrong, r.right) for r in self.recapitalize)

    @property
    def keep_plural_set(self) -> frozenset[str]:
        """Case-folded ``keep_plural_variants`` for case-insensitive lookup
        in ``is_droppable_plural_variant`` (CUR-03).
        """
        return frozenset(s.lower() for s in self.keep_plural_variants)


# ---------------------------------------------------------------------------
# JSON Schema generators (Pydantic → schemas/*.json)
# ---------------------------------------------------------------------------


def build_removals_schema() -> dict:
    """JSON Schema for ``schemas/removals.schema.json`` (LLM ``--json-schema``)."""
    return RemovalsResponse.model_json_schema()


def build_recapitalizations_schema() -> dict:
    """JSON Schema for ``schemas/recapitalizations.schema.json``."""
    return RecapitalizationsResponse.model_json_schema()


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_curator_overrides(path: Path) -> CuratorOverrides:
    """Load + validate ``fixtures/index_curator_overrides.yaml``.

    Uses ``yaml.safe_load`` only (rejects ``!!python/object`` and other
    arbitrary tags — Phase 7 threat model T-07-01-02).

    Raises:
        CuratorFixtureError: file missing, YAML parse error, Pydantic
            validation error, or curator-gate violation
            (``metadata.curated_by == "PENDING_AUTHOR"``).
    """
    if not path.exists():
        raise CuratorFixtureError(
            f"curator fixture not found at {path!s}. "
            f"Wave 1 ships a skeleton; Wave 2 author signoff lands the "
            f"PENDING_AUTHOR-flipped final."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CuratorFixtureError(
            f"curator fixture YAML parse error at {path!s}: {exc}"
        ) from exc
    if raw is None:
        raise CuratorFixtureError(
            f"curator fixture is empty at {path!s} — expected at minimum a "
            f"`metadata:` block."
        )
    try:
        return CuratorOverrides.model_validate(raw)
    except Exception as exc:  # ValidationError or pydantic ValueError
        # Detect the PENDING_AUTHOR sentinel early for a precise message;
        # other validation errors bubble up wrapped as CuratorFixtureError.
        msg = str(exc)
        if "PENDING_AUTHOR" in msg:
            raise CuratorFixtureError(
                f"curator gate FAIL at {path!s}: metadata.curated_by is the "
                f"PENDING_AUTHOR sentinel — the fixture is unsigned. The "
                f"author MUST replace it with their email + ISO timestamp "
                f"before the build proceeds (Phase 7 CONTEXT D-08).\n\n"
                f"Underlying error: {exc}"
            ) from exc
        raise CuratorFixtureError(
            f"curator fixture validation FAIL at {path!s}: {exc}"
        ) from exc


# =============================================================================
# Phase 8 (v1.2) additions — coverage recovery fixtures
# =============================================================================
#
# Adds Pydantic schemas + YAML loaders for the zero_evidence_drops audit and
# recovery pipeline. Mirrors the Phase 7 curator-gate pattern verbatim:
#
#   - ``ConfigDict(extra="forbid", frozen=True)`` on every class — Lock #2
#     enforcement (zero locator-shaped fields tolerable in LLM-output schemas).
#   - ``_reject_pending_author`` field validator on every metadata block —
#     fail-closed default per CONTEXT D-12.
#   - YAML loaders mirror ``load_curator_overrides`` — yaml.safe_load +
#     ``Model.model_validate`` (no arbitrary tag execution).
#
# requirements_addressed: COV-01 (CoverageAuditEntry), COV-02 (DropEntry +
# ZeroEvidenceDropsClassification + DropClassificationResponse),
# COV-04 (RecoveredTerm + RecoveredTermsSpotCheck — D-11 spot-check fixture).


class DropEntry(BaseModel):
    """One classified drop in the fixture's three-bucket taxonomy. (D-01)"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    canonical_term: str = Field(min_length=1)
    rationale: str = Field(default="")  # Required for acknowledged_deferred per D-02
    suggested_fix: (
        Literal["lemma_override", "variant_loss_patch", "verify_patch", "none"] | None
    ) = None
    discovered_via: Literal["original_141", "heading_probe"] = "original_141"


class _ZEDClassificationMetadataBlock(BaseModel):
    """Curator fixture metadata + signoff gate (mirrors _MetadataBlock pattern)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: int = Field(ge=1)
    curated_by: str = Field(min_length=1)
    curated_at_iso: str = Field(default="")
    source_index_version: str = Field(min_length=1)
    source_index_tree_sha256: str = Field(default="")
    expected_total_drops: int = Field(ge=0)

    @field_validator("curated_by")
    @classmethod
    def _reject_pending_author(cls, v: str) -> str:
        if v.strip() == "PENDING_AUTHOR":
            raise ValueError("PENDING_AUTHOR sentinel; fixture is unsigned.")
        return v


class ZeroEvidenceDropsClassification(BaseModel):
    """Top-level fixture container for fixtures/zero_evidence_drops_classification.yaml. (COV-02)"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    metadata: _ZEDClassificationMetadataBlock
    legitimate_coverage_gap: list[DropEntry] = Field(default_factory=list)
    correct_rejections: list[DropEntry] = Field(default_factory=list)
    acknowledged_deferred: list[DropEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_canonicals(self) -> ZeroEvidenceDropsClassification:
        all_terms = (
            [d.canonical_term for d in self.legitimate_coverage_gap]
            + [d.canonical_term for d in self.correct_rejections]
            + [d.canonical_term for d in self.acknowledged_deferred]
        )
        if len(all_terms) != len(set(all_terms)):
            seen: set[str] = set()
            dups: list[str] = []
            for t in all_terms:
                if t in seen:
                    dups.append(t)
                else:
                    seen.add(t)
            raise ValueError(f"duplicate canonical_term across buckets: {dups}")
        return self

    @model_validator(mode="after")
    def _ack_deferred_requires_rationale(self) -> ZeroEvidenceDropsClassification:
        for d in self.acknowledged_deferred:
            if not d.rationale.strip():
                raise ValueError(
                    f"acknowledged_deferred entry {d.canonical_term!r} requires "
                    f"non-empty rationale (D-02)."
                )
        return self


class DropClassificationResponse(BaseModel):
    """LLM-propose response shape — Lock #2: ZERO locator-shaped fields. (COV-02)"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    legitimate_coverage_gap: list[DropEntry] = Field(default_factory=list)
    correct_rejections: list[DropEntry] = Field(default_factory=list)
    acknowledged_deferred: list[DropEntry] = Field(default_factory=list)


class RecoveredTerm(BaseModel):
    """One spot-check triple for the parametrized invariant (D-11)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    term: str = Field(min_length=1)
    expected_section_ref: str = Field(min_length=1)  # e.g., "§ 3.06.10"
    verified: bool = Field(default=False)
    note: str = Field(default="")


class _RecoveredTermsMetadataBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: int = Field(ge=1)
    curated_by: str = Field(min_length=1)
    curated_at_iso: str = Field(default="")
    source_index_version: str = Field(min_length=1)

    @field_validator("curated_by")
    @classmethod
    def _reject_pending_author(cls, v: str) -> str:
        if v.strip() == "PENDING_AUTHOR":
            raise ValueError("PENDING_AUTHOR sentinel; fixture is unsigned.")
        return v


class RecoveredTermsSpotCheck(BaseModel):
    """Top-level container for fixtures/recovered_terms_spot_check.yaml. (D-11)"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    metadata: _RecoveredTermsMetadataBlock
    triples: list[RecoveredTerm] = Field(default_factory=list)


class CoverageAuditEntry(BaseModel):
    """One row in artifacts/coverage_audit.json. (COV-01)"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    candidate_term: str = Field(min_length=1)
    discovered_via: Literal["original_141", "heading_probe"]
    origin_chapter: str | None = None
    origin_pass: Literal["noun_phrase", "doctrinal", "ner"] | None = None
    literal_occurrence_count: int = Field(ge=0)
    lemma_occurrence_count: int = Field(ge=0)
    plural_form_occurrence_count: int = Field(ge=0)
    nearest_section_heading_match: str | None = None
    classification_hint: Literal[
        "lemma_collision", "bigram_inflected", "noise", "heading_only", "unknown"
    ]
    suggested_fix: Literal[
        "lemma_override", "variant_loss_patch", "verify_patch", "none"
    ]


def load_zero_evidence_drops_classification(
    path: Path,
) -> ZeroEvidenceDropsClassification:
    """Load + validate fixtures/zero_evidence_drops_classification.yaml.

    Mirror of ``load_curator_overrides``: ``yaml.safe_load`` (no arbitrary
    tag execution) + ``model_validate`` (Pydantic strict gate). The
    ``_reject_pending_author`` validator on the metadata block raises
    ``ValueError`` (wrapped as ``ValidationError``) on the Wave 0 sentinel,
    enforcing fail-closed default per D-12.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ZeroEvidenceDropsClassification.model_validate(raw)


def load_recovered_terms_spot_check(path: Path) -> RecoveredTermsSpotCheck:
    """Load + validate fixtures/recovered_terms_spot_check.yaml."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RecoveredTermsSpotCheck.model_validate(raw)


def build_drop_classifications_schema() -> dict:
    """Pydantic-derived JSON schema for ``claude -p --json-schema``. (Lock #2 source)"""
    return DropClassificationResponse.model_json_schema()


def build_coverage_audit_schema() -> dict:
    """Pydantic-derived JSON schema for the coverage-audit row shape. (COV-01)"""
    return CoverageAuditEntry.model_json_schema()


# =============================================================================
# Phase 9 (v1.3) — Editorial Overrides (REND-01)
# =============================================================================
#
# 9 R-class rule records + container + loader. Mirrors the Phase 7 / Phase 8
# curator-gate pattern verbatim:
#
#   - ``ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)``
#     on every class — Lock #2 enforcement; rejects locator-shaped fields at
#     every level (page / pdf_page / section_ref / folio / pp / p).
#   - ``_reject_pending_author`` field validator on ``_EditorialOverridesMetadataBlock``
#     — fail-closed default per CONTEXT D-08.
#   - YAML loader mirrors ``load_curator_overrides`` — yaml.safe_load +
#     ``Model.model_validate`` (no arbitrary tag execution); wraps
#     ``ValidationError`` in ``CuratorFixtureError``.
#   - R2 strict-guard mirrors ``RecapitalizationRecord._strict_guard``:
#     ``wrong.lower() == right.lower()``. CONTEXT D-06 / D-08.
#
# requirements_addressed: REND-01.


class R1StripVariantsRule(BaseModel):
    """R1: scrub the ``variants`` list on the matched canonical (set to [])."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    term: str = Field(min_length=1)
    reason: str = Field(default="")


class R2RecapitalizeRule(BaseModel):
    """R2: acronym caps fix; mirrors ``RecapitalizationRecord`` strict-guard.

    Strict guard: ``wrong.lower() == right.lower()``. Any letter delta raises
    ``ValidationError`` with ``strict_guard_violation`` in the message — caught
    at fixture-load time, before any text is mutated downstream.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    wrong: str = Field(min_length=1)
    right: str = Field(min_length=1)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _strict_guard(self) -> R2RecapitalizeRule:
        if self.wrong.lower() != self.right.lower():
            raise ValueError(
                f"strict_guard_violation: letters changed between "
                f"{self.wrong!r} and {self.right!r} "
                f"(lower forms differ: {self.wrong.lower()!r} != "
                f"{self.right.lower()!r}). "
                f"R2 only allows case mutation."
            )
        return self


class R3RewordRule(BaseModel):
    """R3: rename ``entry.canonical`` from ``before`` to ``after``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    reason: str = Field(default="")


class R4DeleteEntryRule(BaseModel):
    """R4: drop the matched canonical from the rendered list."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    term: str = Field(min_length=1)
    reason: str = Field(default="")


class R5DeleteXrefRule(BaseModel):
    """R5: drop cross-refs whose head equals ``head``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    head: str = Field(min_length=1)
    reason: str = Field(default="")


class R6PromoteSingleChildRule(BaseModel):
    """R6: drop synthesized parent block whose stem equals ``parent_stem``.

    ``promoted_children`` is informational — the apply pass detects which
    children become top-level; the field is here for curator audit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    parent_stem: str = Field(min_length=1)
    promoted_children: list[str] = Field(default_factory=list)
    reason: str = Field(default="")


class R7FoldDoubledWordRule(BaseModel):
    """R7: merge locators of ``artifact`` into ``canonical``; drop ``artifact``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    artifact: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    reason: str = Field(default="")


class R8PluralCanonicalRule(BaseModel):
    """R8: rename canonical from ``singular`` to ``plural``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    singular: str = Field(min_length=1)
    plural: str = Field(min_length=1)
    reason: str = Field(default="")


class R9WhitespaceRule(BaseModel):
    """R9: post-emit text replace; pure string find/replace at render boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    reason: str = Field(default="")


class _EditorialOverridesMetadataBlock(BaseModel):
    """Editorial overrides fixture metadata + curator signoff gate.

    Mirrors ``_ZEDClassificationMetadataBlock``. Has NO
    ``expected_entry_count_band`` — the editorial pass is non-coverage-altering
    (R1..R9 are renaming / deletion / scrubbing; the byte-identity acceptance
    test in Wave 4 is the count-stability check).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: int = Field(ge=1)
    curated_by: str = Field(min_length=1)
    curated_at_iso: str = Field(default="")
    source_index_version: str = Field(min_length=1)
    source_index_sha256: str = Field(default="")

    @field_validator("curated_by")
    @classmethod
    def _reject_pending_author(cls, v: str) -> str:
        if v.strip() == "PENDING_AUTHOR":
            raise ValueError("PENDING_AUTHOR sentinel; fixture is unsigned.")
        return v


class EditorialOverrides(BaseModel):
    """Phase 9 editorial overrides — 9 R-class sibling lists.

    Apply order is locked in ``src/book_indexer/render/editorial_overrides.py``
    (``_APPLY_ORDER`` tuple, landing in Wave 2). Curator does NOT control
    inter-class order via YAML. Lock #2: ``extra="forbid"`` at every level
    rejects any locator-shaped key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    metadata: _EditorialOverridesMetadataBlock
    R1_strip_variants: list[R1StripVariantsRule] = Field(default_factory=list)
    R2_recapitalize: list[R2RecapitalizeRule] = Field(default_factory=list)
    R3_reword: list[R3RewordRule] = Field(default_factory=list)
    R4_delete_entry: list[R4DeleteEntryRule] = Field(default_factory=list)
    R5_delete_xref: list[R5DeleteXrefRule] = Field(default_factory=list)
    R6_promote_single_child: list[R6PromoteSingleChildRule] = Field(default_factory=list)
    R7_fold_doubled_word: list[R7FoldDoubledWordRule] = Field(default_factory=list)
    R8_plural_canonical: list[R8PluralCanonicalRule] = Field(default_factory=list)
    R9_whitespace: list[R9WhitespaceRule] = Field(default_factory=list)


def load_editorial_overrides(path: Path) -> EditorialOverrides:
    """Load + validate the Phase 9 editorial-overrides fixture.

    Uses ``yaml.safe_load`` only (rejects ``!!python/object`` and other
    arbitrary tags — Phase 9 threat model T-09-yaml-deserialization).

    Raises:
        CuratorFixtureError: file missing, YAML parse error, empty content,
            Pydantic validation error, or curator-gate violation
            (``metadata.curated_by == "PENDING_AUTHOR"``).
    """
    if not path.exists():
        raise CuratorFixtureError(
            f"editorial-overrides fixture not found at {path!s}. "
            f"Run `uv run python scripts/extract_v1.2.3_overrides.py` (Wave 3)."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CuratorFixtureError(
            f"editorial-overrides fixture YAML parse FAIL: {path!s}: {exc}"
        ) from exc
    if raw is None:
        raise CuratorFixtureError(
            f"editorial-overrides fixture is empty: {path!s}"
        )
    try:
        return EditorialOverrides.model_validate(raw)
    except Exception as exc:  # ValidationError or pydantic ValueError
        msg = str(exc)
        if "PENDING_AUTHOR" in msg:
            raise CuratorFixtureError(
                f"curator gate FAIL: {path!s} carries PENDING_AUTHOR sentinel. "
                f"Edit metadata.curated_by to a real email + metadata.curated_at_iso "
                f"to release the build."
            ) from exc
        raise CuratorFixtureError(
            f"editorial-overrides fixture validation FAIL: {path!s}: {msg}"
        ) from exc
