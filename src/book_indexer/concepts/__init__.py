"""Public API of the LLM concept-discovery subpackage.

This subpackage proposes **concept candidates only** — term / canonical_form /
variants / example_quote. It is **architecturally incapable of emitting**:

- any page number (``page``, ``pp``, ``folio`` — rejected by Pydantic
  ``extra="forbid"`` and CON-07 field-wide regex; CLAUDE.md Architecture Lock #2).
- any section reference (``section``, ``section_ref``, ``§`` — same gates).
- any import from the Anthropic Python SDK or ``claude_agent_sdk`` — CLAUDE.md
  Architecture Lock #3, enforced by ``tests/invariants/test_no_anthropic_sdk_imports.py``.

All LLM calls go through ``subprocess.run(["claude", "-p", …])`` per CON-01
(amended 2026-04-24 — see REQUIREMENTS.md CON-01 and 03A-CONTEXT.md D-08).

Exports:
- ``ConceptCandidate`` / ``ConceptDiscoveryResponse`` — Plan 03A-02
- ``ConceptDiscoveryError`` and subclasses — Plan 03A-02
- ``run_concept_discovery`` / ``RunSummary`` — Plan 03A-08 CLI-level helper
"""
from __future__ import annotations

from .errors import (
    CacheKeyDriftError,
    ConceptDiscoveryError,
    PromptFileNotFoundError,
    SchemaRetryExhaustedError,
    SubagentAuthError,
    SubagentCliError,
    SubagentTimeoutError,
)
from .passes import (
    PASS_ORDER,
    CallResult,
    build_provenance,
    compute_corpus_sha,
    compute_pattern_sha,
    compute_spacy_model_sha,
    run_all_symbolic,
    write_pass_artifact,
)
from .schema import (
    ClaudeCliEnvelope,
    ConceptCandidate,
    ConceptDiscoveryResponse,
    NoLocatorStr,
    build_json_schema,
)
from .symbolic import (
    build_doctrinal_nlp,
    extract_doctrinal,
    extract_ner,
    extract_noun_phrases,
    load_doctrinal_patterns,
)

__all__ = [
    "PASS_ORDER",
    "CacheKeyDriftError",
    "CallResult",
    "ClaudeCliEnvelope",
    "ConceptCandidate",
    "ConceptDiscoveryError",
    "ConceptDiscoveryResponse",
    "NoLocatorStr",
    "PromptFileNotFoundError",
    "SchemaRetryExhaustedError",
    "SubagentAuthError",
    "SubagentCliError",
    "SubagentTimeoutError",
    "build_doctrinal_nlp",
    "build_json_schema",
    "build_provenance",
    "compute_corpus_sha",
    "compute_pattern_sha",
    "compute_spacy_model_sha",
    "extract_doctrinal",
    "extract_ner",
    "extract_noun_phrases",
    "load_doctrinal_patterns",
    "run_all_symbolic",
    "write_pass_artifact",
]
