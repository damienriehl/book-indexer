"""Phase 3b — Tables of Cases, Statutes, and Rules.

Pure-symbolic citation extraction (eyecite + regex) routed through Phase 2
``verify()`` for dual-locator emission. NO LLM in this phase.

Public API includes the IR, alphabetization, errors (Plan 01), the
extractors (Plan 02), and the orchestration shims (Plan 03 — verifier_bridge,
resolver, build/replay CLI).
"""
from .alphabetize import STRIP_SET, sort_key
from .errors import (
    ChapterRuleSystemError,
    JurisdictionNotEnabledError,
    TableExtractionError,
)
from .ir import (
    CaseEntry,
    Locator,
    RuleEntry,
    StatuteEntry,
    SubsectionEntry,
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
    TableProvenance,
)
from .resolver import UnresolvedCiteRecord, resolve_chapter
from .verifier_bridge import verify_case, verify_rule, verify_statute

__all__ = [
    "TableExtractionError",
    "JurisdictionNotEnabledError",
    "ChapterRuleSystemError",
    "Locator",
    "CaseEntry",
    "StatuteEntry",
    "SubsectionEntry",
    "RuleEntry",
    "TableProvenance",
    "TableOfCases",
    "TableOfStatutes",
    "TableOfRules",
    "sort_key",
    "STRIP_SET",
    "UnresolvedCiteRecord",
    "resolve_chapter",
    "verify_case",
    "verify_rule",
    "verify_statute",
]
