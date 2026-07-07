"""Typed exception hierarchy for the tables/ pipeline.

Plan 02 (regex_fallback, cases, statutes, rules) and Plan 03 (resolver,
verifier_bridge, __main__) consume these. NO module here imports from
other tables/ submodules — keeps this file safe to import from anywhere
in the package without circular dependencies.
"""
from __future__ import annotations


class TableExtractionError(Exception):
    """Base class for all Phase 3b extractor errors."""


class JurisdictionNotEnabledError(TableExtractionError):
    """Raised when an extractor encounters a citation whose jurisdiction
    is not enabled in ``fixtures/citation_jurisdictions.yaml``.

    Per D-07, only ``us`` (USC + Federal Constitution) is enabled for
    the reference corpus. Adding a jurisdiction requires re-running the
    enumeration probe and a CONTEXT amendment.
    """


class ChapterRuleSystemError(TableExtractionError):
    """Raised when ``fixtures/chapter_rule_systems.yaml`` is missing,
    malformed, or still in DRAFT state (``metadata.curated_by ==
    'PENDING_AUTHOR'``).

    Per D-06, the chapter→rule_system mapping is hand-curated by the
    author. Wave 0 is the gate; this error is the trip-wire if a build
    starts before Wave 0 closes.
    """
