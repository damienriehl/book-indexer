"""COV-01 audit subpackage (Phase 8 / Wave 1).

Public surface:
    - heading_probe_candidates / ProbeCandidate / LEAD_VERBS / COMMON_STOPS
    - count_occurrences / derive_suggested_fix
    - write_coverage_audit_json / write_coverage_audit_csv
    - AUDIT_FIELDS

This package is a READ-ONLY consumer of corpus + index_tree artifacts. It NEVER
constructs ``Evidence`` (Architecture Lock #1; CI-enforced by
``tests/invariants/test_verify_is_sole_locator_source.py``).
"""
from .coverage_audit import (
    AUDIT_FIELDS,
    count_occurrences,
    derive_suggested_fix,
    write_coverage_audit_csv,
    write_coverage_audit_json,
)
from .heading_probe import (
    COMMON_STOPS,
    LEAD_VERBS,
    ProbeCandidate,
    heading_probe_candidates,
)

__all__ = [
    "AUDIT_FIELDS",
    "COMMON_STOPS",
    "LEAD_VERBS",
    "ProbeCandidate",
    "count_occurrences",
    "derive_suggested_fix",
    "heading_probe_candidates",
    "write_coverage_audit_csv",
    "write_coverage_audit_json",
]
