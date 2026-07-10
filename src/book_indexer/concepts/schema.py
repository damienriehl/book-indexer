"""Phase 3a schema contract — LLM cannot emit a locator (CON-02, CON-07, Lock #2).

This module is the SOLE place a Phase 3a candidate shape is defined. The CLI
(`claude -p --json-schema <build_json_schema()>`) and Python
(`ConceptDiscoveryResponse.model_validate(envelope.structured_output)`) both
consume this schema — defense-in-depth.

Key invariants:

1. ``extra="forbid"`` on every model — rejects any unrecognized field
   (CLAUDE.md Architecture Lock #2; ship-blocker test D-18/D-19).
2. ``frozen=True`` on every model — immutable Pydantic instances, matches
   Phase 2 ``Evidence`` pattern (src/book_indexer/verify/evidence.py).
3. ``NoLocatorStr`` Annotated type — rejects any string value starting with
   ``§`` or ``N.NN`` at EVERY occurrence (CON-07 D-05 field-wide rule). Uses
   ``AfterValidator`` (Python ``re``; lookahead supported) + ``json_schema_extra``
   (ECMA-262 lookahead; accepted by ``claude -p --json-schema``) per H-2.
4. ``variants: list[NoLocatorStr]`` — item-type validation triggers per H-3.
5. ``ClaudeCliEnvelope`` marks ``structured_output: dict | None`` optional-
   independent of ``is_error`` per H-8.

requirements_addressed: CON-02, CON-07
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_validators import AfterValidator

# D-03 concept ``kind`` enum, shared with ``symbolic.kind_for_match``.
ConceptKind = Literal["doctrine", "rule", "procedure", "concept", "actor", "instrument"]

# ---------------------------------------------------------------------------
# CON-07 / D-05: field-wide locator-prefix rejection
# ---------------------------------------------------------------------------
# Pydantic's native ``Field(pattern=...)`` uses the Rust ``regex`` crate which
# does NOT support lookahead. We therefore:
#   (a) express the rejection as a Python ``re`` post-validator
#       (lookahead supported in stdlib ``re``); AND
#   (b) emit the JSON-Schema ``pattern`` keyword via ``json_schema_extra`` so
#       the CLI's server-side validator (ECMA-262) also catches violations.
# Hazard H-2 rationale: see 03A-RESEARCH.md lines 1075-1082.

_PY_LOCATOR_PREFIX = re.compile(r"^(§|\d{1,2}\.\d{2})")
_JSON_SCHEMA_PATTERN = r"^(?!§|\d{1,2}\.\d{2}).*"


def _reject_locator_prefix(v: str) -> str:
    """CON-07 value-based check. Empty strings allowed (CON-02 min_length enforces)."""
    if v and _PY_LOCATOR_PREFIX.match(v):
        raise ValueError(f"locator-prefix forbidden: {v!r}")
    return v


NoLocatorStr = Annotated[
    str,
    AfterValidator(_reject_locator_prefix),
    Field(json_schema_extra={"pattern": _JSON_SCHEMA_PATTERN}),
]
"""Reusable Annotated type with CON-07 enforced on every occurrence.

Do NOT combine with ``Field(pattern=...)`` at the same layer — the Rust regex
engine would crash on the lookahead at import time (H-2).
"""


# ---------------------------------------------------------------------------
# D-02 / D-03: ConceptCandidate
# ---------------------------------------------------------------------------


class ConceptCandidate(BaseModel):
    """A single concept proposed by a Phase-3a subagent.

    Fields exactly per D-02 (required) + D-03 (optional). Any extra field,
    including the ``example_page_hint`` mentioned in ARCHITECTURE §5.2 (which
    is SUPERSEDED per D-01), triggers ``extra_forbidden`` ValidationError.

    requirements_addressed: CON-02, CON-07
    """

    model_config = ConfigDict(
        extra="forbid",         # Architecture Lock #2
        frozen=True,            # matches Phase 2's Evidence pattern
        str_strip_whitespace=False,  # example_quote is verbatim book prose
    )

    term: Annotated[NoLocatorStr, Field(min_length=2)]
    canonical_form: Annotated[NoLocatorStr, Field(min_length=2)]
    variants: Annotated[list[NoLocatorStr], Field(default_factory=list, max_length=10)]
    example_quote: Annotated[NoLocatorStr, Field(max_length=200)]
    kind: ConceptKind | None = None
    suggested_subentries: list[NoLocatorStr] | None = None


# ---------------------------------------------------------------------------
# D-04: response envelope
# ---------------------------------------------------------------------------


class ConceptDiscoveryResponse(BaseModel):
    """Root envelope returned by a ``claude -p --json-schema ...`` call.

    Shape per D-04. ``schema_version`` pins against silent re-introduction of
    dropped fields (D-01). ``pass_type`` is envelope-level (per D-09 the
    Pydantic model is shared across all 4 passes).

    requirements_addressed: CON-02, CON-04
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Annotated[str, Field(min_length=1)]
    pass_type: Literal["noun_phrase", "doctrinal", "ner", "implicit"]
    chunk_id: Annotated[str, Field(pattern=r"^ch\d+$")]  # "ch1".."ch5"
    candidates: list[ConceptCandidate]


# ---------------------------------------------------------------------------
# H-8: CLI envelope parser
# ---------------------------------------------------------------------------


class ClaudeCliEnvelope(BaseModel):
    """Parsed ``claude -p --output-format json`` envelope.

    H-8: ``structured_output`` is present-or-absent INDEPENDENTLY of
    ``is_error``. Downstream code must check both; do NOT assume
    ``is_error=False`` implies ``structured_output is not None``.

    Unknown fields are allowed (``extra="allow"``) because the CLI envelope
    shape evolves across versions; our schema is NOT trying to pin every
    metadata field the CLI emits (we only care about the two booleans and
    the payload). Contrast with ``ConceptDiscoveryResponse`` whose extra
    fields are forbidden — that model IS the contract with the LLM.

    requirements_addressed: CON-01
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    type: str = "result"
    subtype: str = "success"
    is_error: bool = False
    result: str | None = None
    structured_output: dict | None = None
    stop_reason: str | None = None
    duration_ms: int | None = None
    num_turns: int | None = None


# ---------------------------------------------------------------------------
# JSON Schema emission for ``claude -p --json-schema``
# ---------------------------------------------------------------------------


def build_json_schema() -> dict:
    """Return the exact schema to pass to ``claude -p --json-schema``.

    Pydantic 2.9+ emits a Draft-2020-12-style schema with ``$defs`` for nested
    models, ``additionalProperties: false`` at every level (because
    ``extra="forbid"``), and no ``$schema`` field. The CLI accepts this
    format without complaint per empirical probe (03A-RESEARCH.md line 106).

    The returned dict is the unaltered output of
    ``ConceptDiscoveryResponse.model_json_schema()``. Serialize with
    ``orjson.dumps(schema, option=orjson.OPT_SORT_KEYS)`` in callers for
    byte-stable transmission across Python processes.
    """
    return ConceptDiscoveryResponse.model_json_schema()
