"""Phase 7 curator-pass package — curator-gated quality polish (v1.1).

Public API re-exports for callers (Wave 3 render extensions + Wave 4 invariant
tests + Wave 2 propose_*.py scripts).

Phase 8 (v1.2) extension: re-exports for the coverage-recovery fixture schemas
(DropEntry, ZeroEvidenceDropsClassification, DropClassificationResponse,
RecoveredTerm, RecoveredTermsSpotCheck, CoverageAuditEntry) + their YAML loaders
+ Pydantic-derived JSON-schema generators.

requirements_addressed: CUR-01, CUR-02, CUR-03, COV-01, COV-02, COV-04.
"""
from __future__ import annotations

from .apply_recapitalize import apply_recap_pairs, assert_letters_only
from .apply_removal import is_removed
from .errors import CuratorFixtureError, RecapitalizeGuardError
from .fixture import (
    CoverageAuditEntry,
    CuratorOverrides,
    DropClassificationResponse,
    DropEntry,
    RecapitalizationRecord,
    RecapitalizationsResponse,
    RecoveredTerm,
    RecoveredTermsSpotCheck,
    RemovalRecord,
    RemovalsResponse,
    SpotCheckRecord,
    ZeroEvidenceDropsClassification,
    build_coverage_audit_schema,
    build_drop_classifications_schema,
    build_recapitalizations_schema,
    build_removals_schema,
    load_curator_overrides,
    load_recovered_terms_spot_check,
    load_zero_evidence_drops_classification,
)
from .personas import LEGAL_INDEXER_PERSONA
from .plural_filter import is_droppable_plural_variant

__all__ = [
    "CoverageAuditEntry",
    "CuratorFixtureError",
    "CuratorOverrides",
    "DropClassificationResponse",
    "DropEntry",
    "LEGAL_INDEXER_PERSONA",
    "RecapitalizationRecord",
    "RecapitalizationsResponse",
    "RecapitalizeGuardError",
    "RecoveredTerm",
    "RecoveredTermsSpotCheck",
    "RemovalRecord",
    "RemovalsResponse",
    "SpotCheckRecord",
    "ZeroEvidenceDropsClassification",
    "apply_recap_pairs",
    "assert_letters_only",
    "build_coverage_audit_schema",
    "build_drop_classifications_schema",
    "build_recapitalizations_schema",
    "build_removals_schema",
    "is_droppable_plural_variant",
    "is_removed",
    "load_curator_overrides",
    "load_recovered_terms_spot_check",
    "load_zero_evidence_drops_classification",
]
